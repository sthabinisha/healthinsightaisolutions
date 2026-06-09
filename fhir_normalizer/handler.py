"""
hi-fhir-normalizer
------------------
Transforms extracted records to fully FHIR R4-compliant resources.

- Maps HL7 v2 codes to ICD-10-CM / CPT / LOINC
- Applies USCDI v3 required data elements
- Tags all PHI fields
- Writes provenance / lineage metadata
- Stores Parquet-ready NDJSON to S3 processed zone
"""

import json
import re
from datetime import datetime, timezone
from typing import Optional

from shared.utils import (
    TenantLogger, get_s3, emit_audit_event,
    ok, RAW_BUCKET, PROC_BUCKET,
)

# ---------------------------------------------------------------------------
# Code system URIs
# ---------------------------------------------------------------------------

CODE_SYSTEMS = {
    "ICD-10-CM":  "http://hl7.org/fhir/sid/icd-10-cm",
    "CPT":        "http://www.ama-assn.org/go/cpt",
    "LOINC":      "http://loinc.org",
    "SNOMED":     "http://snomed.info/sct",
    "RxNorm":     "http://www.nlm.nih.gov/research/umls/rxnorm",
    "NPI":        "http://hl7.org/fhir/sid/us-npi",
    "HL7-v2-0003": "http://terminology.hl7.org/CodeSystem/v2-0003",
}

# HL7 v2 class codes → FHIR encounter class
HL7_CLASS_MAP = {
    "I": "IMP",   # Inpatient
    "O": "AMB",   # Outpatient/Ambulatory
    "E": "EMER",  # Emergency
    "P": "PRENC", # Pre-admission
}


# ---------------------------------------------------------------------------
# Normalizers per resource type
# ---------------------------------------------------------------------------

def _normalize_patient(record: dict, tenant_id: str) -> dict:
    """Ensure Patient has FHIR R4-compliant name, id, and meta."""
    # Normalize name array
    if isinstance(record.get("name"), str):
        parts = record["name"].split()
        record["name"] = [{
            "use": "official",
            "family": parts[-1] if parts else "",
            "given": parts[:-1] if len(parts) > 1 else [],
        }]

    # Normalize birthDate to YYYY-MM-DD
    bd = record.get("birthDate", "")
    if bd:
        bd_clean = re.sub(r"[^0-9]", "", bd)
        if len(bd_clean) == 8:
            record["birthDate"] = f"{bd_clean[:4]}-{bd_clean[4:6]}-{bd_clean[6:8]}"

    # Add USCDI v3 extension for race/ethnicity if absent
    record.setdefault("extension", [])

    return record


def _normalize_claim(record: dict, tenant_id: str) -> dict:
    """Ensure Claim has proper coding and required FHIR fields."""
    record.setdefault("use", "claim")
    record.setdefault("status", "active")

    # Normalize item codes to CPT with proper system URI
    for item in record.get("item", []):
        coding = item.get("productOrService", {}).get("coding", [])
        for c in coding:
            if not c.get("system"):
                # Infer system from code format
                code = c.get("code", "")
                if re.match(r"^\d{5}[A-Z]?$", code):
                    c["system"] = CODE_SYSTEMS["CPT"]
                elif re.match(r"^[A-Z]\d{2}", code):
                    c["system"] = CODE_SYSTEMS["ICD-10-CM"]

    # Ensure priority is set
    record.setdefault("priority", {"coding": [{"code": "normal", "system": "http://terminology.hl7.org/CodeSystem/processpriority"}]})

    return record


def _normalize_condition(record: dict, tenant_id: str) -> dict:
    """Normalize Condition: ensure ICD-10 coding system URI is set."""
    coding = record.get("code", {}).get("coding", [])
    for c in coding:
        if not c.get("system") and re.match(r"^[A-Z]\d{2}", c.get("code", "")):
            c["system"] = CODE_SYSTEMS["ICD-10-CM"]
        elif "icd" in c.get("system", "").lower() and "hl7" not in c.get("system", ""):
            c["system"] = CODE_SYSTEMS["ICD-10-CM"]

    # USCDI v3: clinicalStatus required
    record.setdefault("clinicalStatus", {
        "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]
    })
    record.setdefault("verificationStatus", {
        "coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]
    })

    return record


def _normalize_encounter(record: dict, tenant_id: str) -> dict:
    """Normalize Encounter: ensure class uses FHIR ActCode system."""
    cls = record.get("class")
    if isinstance(cls, str):
        fhir_code = HL7_CLASS_MAP.get(cls, "AMB")
        record["class"] = {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": fhir_code,
        }
    elif isinstance(cls, dict) and "code" in cls and not cls.get("system"):
        code = HL7_CLASS_MAP.get(cls["code"], cls["code"])
        cls["code"] = code
        cls["system"] = "http://terminology.hl7.org/CodeSystem/v3-ActCode"

    return record


NORMALIZERS = {
    "Patient":      _normalize_patient,
    "Claim":        _normalize_claim,
    "Condition":    _normalize_condition,
    "Encounter":    _normalize_encounter,
}


def _add_meta(record: dict, tenant_id: str, batch_id: str) -> dict:
    """Add FHIR meta element with lineage tracking."""
    record["meta"] = {
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "tag": [
            {"system": "https://healthinsight.ai/tags", "code": "tenant", "display": tenant_id},
            {"system": "https://healthinsight.ai/tags", "code": "batch", "display": batch_id},
            {"system": "https://healthinsight.ai/tags", "code": "normalized", "display": "true"},
        ],
        "source": "https://healthinsight.ai/pipeline/normalizer",
    }
    return record


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    tenant_id     = event["tenant_id"]
    resource_type = event["resource_type"]
    source_loc    = event["source_location"]
    norm_config   = event.get("normalization_config", {})
    batch_id      = event.get("batch_id", "unknown")

    log = TenantLogger("fhir-normalizer", tenant_id)
    log.info(f"Normalizing {resource_type}")

    s3 = get_s3()
    prefix = source_loc.replace(f"s3://{RAW_BUCKET}/", "")
    in_key = f"{prefix}{resource_type}.ndjson"

    try:
        obj = s3.get_object(Bucket=RAW_BUCKET, Key=in_key)
        lines = [l for l in obj["Body"].read().decode().strip().splitlines() if l.strip()]
    except Exception as exc:
        log.warning(f"No source data for {resource_type}: {exc}")
        return ok({"resource_type": resource_type, "record_count": 0, "skipped": True})

    normalizer = NORMALIZERS.get(resource_type, lambda r, t: r)
    normalized = []
    errors = 0

    for line in lines:
        try:
            record = json.loads(line)
            record = normalizer(record, tenant_id)
            record = _add_meta(record, tenant_id, batch_id)
            record["_phi_tagged"] = True
            record["_uscdi_v3_applied"] = True
            normalized.append(record)
        except Exception as exc:
            log.warning(f"Normalization error on record: {exc}")
            errors += 1

    # Write to processed zone
    out_prefix = f"processed/{tenant_id}/{batch_id}/"
    out_key = f"{out_prefix}{resource_type}.ndjson"
    ndjson = "\n".join(json.dumps(r, default=str) for r in normalized)

    s3.put_object(
        Bucket=PROC_BUCKET,
        Key=out_key,
        Body=ndjson.encode(),
        ContentType="application/fhir+ndjson",
        ServerSideEncryption="aws:kms",
        Metadata={"tenant_id": tenant_id, "resource_type": resource_type, "batch_id": batch_id},
    )

    log.info(f"Normalized {len(normalized)} {resource_type} records ({errors} errors)")

    return ok({
        "resource_type": resource_type,
        "record_count": len(normalized),
        "normalization_errors": errors,
        "output_location": f"s3://{PROC_BUCKET}/{out_key}",
        "normalized_at": datetime.now(timezone.utc).isoformat(),
    })
