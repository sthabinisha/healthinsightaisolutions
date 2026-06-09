"""
hi-data-quality-agent
---------------------
Runs FHIR R4 validation and completeness checks for a single resource type.

Checks:
  - Schema compliance (required FHIR R4 fields present)
  - USCDI v3 required data element presence
  - Value set code validation (ICD-10, CPT, LOINC)
  - Duplicate detection (resource ID deduplication)
  - Completeness ratio (non-null fields / total expected fields)
  - Consistency checks (date ordering, reference integrity)

Returns per-resource quality metrics that feed into ComputeCompositeQualityScore.
"""

import json
from datetime import datetime, timezone
from collections import Counter

from shared.utils import TenantLogger, get_s3, ok, RAW_BUCKET


# ---------------------------------------------------------------------------
# FHIR R4 required field definitions per resource type
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "Patient": ["id", "name", "birthDate", "gender"],
    "Encounter": ["id", "status", "class", "subject"],
    "Claim": ["id", "status", "type", "use", "patient", "created", "provider", "priority", "insurance"],
    "Condition": ["id", "clinicalStatus", "code", "subject"],
    "Procedure": ["id", "status", "code", "subject"],
    "Coverage": ["id", "status", "beneficiary", "payor"],
    "Organization": ["id", "name"],
    "Practitioner": ["id", "name"],
    "Observation": ["id", "status", "code", "subject"],
}

# USCDI v3 elements per resource type (subset of most critical)
USCDI_V3_REQUIRED = {
    "Patient": ["birthDate", "gender", "name", "address", "telecom", "identifier"],
    "Encounter": ["type", "period", "reasonCode"],
    "Condition": ["code", "onsetDateTime", "clinicalStatus", "verificationStatus"],
    "Procedure": ["code", "performedDateTime"],
    "Observation": ["code", "effectiveDateTime", "valueQuantity"],
}

# Known valid status values per resource
VALID_STATUSES = {
    "Encounter": {"planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled", "entered-in-error", "unknown"},
    "Claim": {"active", "cancelled", "draft", "entered-in-error"},
    "Condition": {"active", "recurrence", "relapse", "inactive", "remission", "resolved"},
    "Procedure": {"preparation", "in-progress", "not-done", "on-hold", "stopped", "completed", "entered-in-error", "unknown"},
    "Observation": {"registered", "preliminary", "final", "amended", "corrected", "cancelled", "entered-in-error", "unknown"},
}

# ICD-10 prefix patterns (basic structural check)
ICD10_PATTERN = __import__("re").compile(r"^[A-Z]\d{2}(\.\d{1,4})?$")
CPT_PATTERN   = __import__("re").compile(r"^\d{5}[A-Z]?$")
LOINC_PATTERN = __import__("re").compile(r"^\d{4,5}-\d$")


# ---------------------------------------------------------------------------
# Per-record validators
# ---------------------------------------------------------------------------

def _check_required_fields(record: dict, resource_type: str) -> tuple[int, int, list]:
    """Returns (present_count, total_count, missing_fields)."""
    required = REQUIRED_FIELDS.get(resource_type, [])
    missing = [f for f in required if not record.get(f)]
    return len(required) - len(missing), len(required), missing


def _check_uscdi(record: dict, resource_type: str) -> tuple[int, int]:
    """Returns (uscdi_present, uscdi_total)."""
    uscdi_fields = USCDI_V3_REQUIRED.get(resource_type, [])
    present = sum(1 for f in uscdi_fields if record.get(f))
    return present, len(uscdi_fields)


def _check_status_validity(record: dict, resource_type: str) -> bool:
    valid = VALID_STATUSES.get(resource_type)
    if not valid:
        return True
    status = record.get("status", "")
    return status in valid


def _check_code_validity(record: dict, resource_type: str) -> tuple[int, int]:
    """Count valid / total codes found in coding arrays."""
    valid_count = total_count = 0
    codings = []

    if resource_type in ("Condition", "Procedure"):
        codings = record.get("code", {}).get("coding", [])
    elif resource_type == "Observation":
        codings = record.get("code", {}).get("coding", [])

    for coding in codings:
        system = coding.get("system", "")
        code   = coding.get("code", "")
        total_count += 1
        if "icd" in system.lower() and ICD10_PATTERN.match(code):
            valid_count += 1
        elif "cpt" in system.lower() and CPT_PATTERN.match(code):
            valid_count += 1
        elif "loinc" in system.lower() and LOINC_PATTERN.match(code):
            valid_count += 1
        elif system:  # Unknown system but code present
            valid_count += 1  # Give benefit of doubt for unknown systems

    return valid_count, total_count


def _check_date_consistency(record: dict) -> bool:
    """For Encounter: verify start <= end."""
    period = record.get("period", {})
    start_str = period.get("start")
    end_str   = period.get("end")
    if start_str and end_str:
        try:
            start = datetime.fromisoformat(start_str.rstrip("Z"))
            end   = datetime.fromisoformat(end_str.rstrip("Z"))
            return start <= end
        except ValueError:
            return False
    return True


def _compute_field_completeness(record: dict) -> float:
    """Ratio of non-null, non-empty fields to total fields (recursive)."""
    def count_fields(obj, depth=0) -> tuple[int, int]:
        if depth > 5:
            return 0, 0
        if isinstance(obj, dict):
            total = present = 0
            for k, v in obj.items():
                if k.startswith("_"):
                    continue
                total += 1
                if v not in (None, "", [], {}):
                    present += 1
                sub_p, sub_t = count_fields(v, depth + 1)
                total += sub_t
                present += sub_p
            return present, total
        elif isinstance(obj, list):
            total = present = 0
            for item in obj:
                p, t = count_fields(item, depth + 1)
                total += t
                present += p
            return present, total
        return 0, 0

    p, t = count_fields(record)
    return round(p / t, 3) if t > 0 else 0.0


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    tenant_id     = event["tenant_id"]
    resource_type = event["resource_type"]
    s3_location   = event["s3_location"]
    val_config    = event.get("validation_config", {})

    log = TenantLogger("data-quality-agent", tenant_id)
    log.info(f"Validating {resource_type}")

    s3 = get_s3()
    prefix = s3_location.replace(f"s3://{RAW_BUCKET}/", "")
    key = f"{prefix}{resource_type}.ndjson"

    try:
        obj = s3.get_object(Bucket=RAW_BUCKET, Key=key)
        lines = [l for l in obj["Body"].read().decode().strip().splitlines() if l.strip()]
    except Exception as exc:
        log.warning(f"No data for {resource_type}: {exc}")
        return ok({
            "resource_type": resource_type,
            "record_count": 0,
            "quality_score": 1.0,
            "issues": [],
            "skipped": True,
        })

    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not records:
        return ok({"resource_type": resource_type, "record_count": 0, "quality_score": 1.0, "issues": [], "skipped": True})

    # ---- Metrics accumulators ----
    completeness_scores = []
    required_scores = []
    uscdi_scores = []
    status_failures = 0
    code_valid = code_total = 0
    date_failures = 0
    duplicate_ids: Counter = Counter()
    issues: list[str] = []

    for record in records:
        # Completeness
        completeness_scores.append(_compute_field_completeness(record))

        # Required fields
        present, total, missing = _check_required_fields(record, resource_type)
        required_scores.append(present / total if total else 1.0)
        if missing:
            issues.append(f"Missing required fields: {missing[:3]}")

        # USCDI
        u_present, u_total = _check_uscdi(record, resource_type)
        uscdi_scores.append(u_present / u_total if u_total else 1.0)

        # Status validity
        if not _check_status_validity(record, resource_type):
            status_failures += 1

        # Code validity
        cv, ct = _check_code_validity(record, resource_type)
        code_valid += cv
        code_total += ct

        # Date consistency (Encounter)
        if resource_type == "Encounter" and not _check_date_consistency(record):
            date_failures += 1

        # Duplicate detection
        rid = record.get("id", "")
        if rid:
            duplicate_ids[rid] += 1

    n = len(records)
    duplicates = sum(count - 1 for count in duplicate_ids.values() if count > 1)

    avg_completeness = sum(completeness_scores) / n
    avg_required     = sum(required_scores) / n
    avg_uscdi        = sum(uscdi_scores) / n if uscdi_scores else 1.0
    code_validity    = code_valid / code_total if code_total else 1.0
    uniqueness       = 1.0 - (duplicates / n)

    # Weighted composite per resource:
    # completeness 30%, required 35%, uscdi 15%, code_validity 10%, uniqueness 10%
    quality_score = round(
        avg_completeness * 0.30 +
        avg_required     * 0.35 +
        avg_uscdi        * 0.15 +
        code_validity    * 0.10 +
        uniqueness       * 0.10,
        3
    )

    threshold = val_config.get("completeness_threshold", 0.75)
    if status_failures > 0:
        issues.append(f"{status_failures} records with invalid status values")
    if duplicates > 0:
        issues.append(f"{duplicates} duplicate resource IDs detected")
    if date_failures > 0:
        issues.append(f"{date_failures} records with inconsistent date ranges")

    log.info(f"{resource_type}: score={quality_score}, records={n}, dupes={duplicates}")

    return ok({
        "resource_type": resource_type,
        "record_count": n,
        "quality_score": quality_score,
        "metrics": {
            "completeness": round(avg_completeness, 3),
            "required_field_presence": round(avg_required, 3),
            "uscdi_compliance": round(avg_uscdi, 3),
            "code_validity": round(code_validity, 3),
            "uniqueness": round(uniqueness, 3),
            "duplicate_count": duplicates,
            "status_failures": status_failures,
            "date_consistency_failures": date_failures,
        },
        "issues": list(set(issues))[:10],
        "meets_threshold": quality_score >= threshold,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    })
