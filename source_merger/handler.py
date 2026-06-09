"""
hi-source-merger
----------------
Deduplicates and merges records from multiple extraction sources
(FHIR + HL7) using deterministic patient matching on MRN + DOB + name.

This state runs after ExtractFromMultipleSources parallel branch.
"""

import json
import hashlib
from datetime import datetime, timezone

from shared.utils import TenantLogger, get_s3, emit_audit_event, ok, RAW_BUCKET


def _normalize_name(name_str: str) -> str:
    """Lowercase, strip, remove punctuation for fuzzy matching."""
    import re
    return re.sub(r"[^a-z0-9]", "", (name_str or "").lower())


def _patient_fingerprint(resource: dict) -> str:
    """
    Deterministic fingerprint for patient matching:
    MRN + DOB + normalized family name.
    """
    mrn  = resource.get("id", "") or ""
    dob  = resource.get("birthDate", "") or ""
    name = resource.get("name", [{}])
    family = _normalize_name(name[0].get("family", "") if name else "")
    raw = f"{mrn}|{dob}|{family}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_ndjson_from_s3(s3, bucket: str, prefix: str, resource_type: str) -> list[dict]:
    key = f"{prefix}{resource_type}.ndjson"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        lines = obj["Body"].read().decode().strip().splitlines()
        return [json.loads(l) for l in lines if l.strip()]
    except s3.exceptions.NoSuchKey:
        return []


def _merge_resources(primary: list[dict], secondary: list[dict], resource_type: str) -> tuple[list, int]:
    """
    Merge two lists of FHIR resources, deduplicating on id where possible.
    Returns (merged_list, duplicate_count).
    """
    if resource_type == "Patient":
        seen: dict[str, dict] = {}
        for r in primary + secondary:
            fp = _patient_fingerprint(r)
            if fp not in seen:
                seen[fp] = r
            else:
                # Merge: prefer the record with more populated fields
                existing = seen[fp]
                if len(json.dumps(r)) > len(json.dumps(existing)):
                    seen[fp] = r
        dupes = len(primary) + len(secondary) - len(seen)
        return list(seen.values()), dupes
    else:
        # For non-Patient resources: deduplicate on resource id
        seen: dict[str, dict] = {}
        for r in primary + secondary:
            rid = r.get("id", id(r))
            if rid not in seen:
                seen[rid] = r
        dupes = len(primary) + len(secondary) - len(seen)
        return list(seen.values()), dupes


def handler(event: dict, context) -> dict:
    tenant_id      = event["tenant_id"]
    source_results = event["source_results"]   # [fhir_result, hl7_result]

    log = TenantLogger("source-merger", tenant_id)

    s3 = get_s3()
    merged_prefix = f"raw/{tenant_id}/merged/"

    resource_types = set()
    for r in source_results:
        for rt in r.get("resource_types_extracted", []):
            resource_types.add(rt)

    total_dupes = 0
    merged_counts: dict[str, int] = {}
    merged_types: list[str] = []

    for rt in resource_types:
        lists = []
        for r in source_results:
            prefix = r.get("s3_location", "").replace(f"s3://{RAW_BUCKET}/", "")
            records = _load_ndjson_from_s3(s3, RAW_BUCKET, prefix, rt)
            if records:
                lists.append(records)

        if len(lists) == 0:
            continue
        if len(lists) == 1:
            merged, dupes = lists[0], 0
        else:
            merged, dupes = _merge_resources(lists[0], lists[1], rt)

        total_dupes += dupes
        merged_counts[rt] = len(merged)
        merged_types.append(rt)

        # Write merged NDJSON
        key = f"{merged_prefix}{rt}.ndjson"
        ndjson = "\n".join(json.dumps(r, default=str) for r in merged)
        s3.put_object(
            Bucket=RAW_BUCKET, Key=key, Body=ndjson.encode(),
            ContentType="application/fhir+ndjson", ServerSideEncryption="aws:kms",
            Metadata={"tenant_id": tenant_id},
        )
        log.info(f"Merged {rt}: {len(merged)} records ({dupes} duplicates removed)")

    emit_audit_event(tenant_id, "SOURCES_MERGED", {
        "resource_types": merged_types,
        "merged_counts": merged_counts,
        "duplicates_removed": total_dupes,
    })

    return ok({
        "s3_location": f"s3://{RAW_BUCKET}/{merged_prefix}",
        "resource_types_extracted": merged_types,
        "record_counts": merged_counts,
        "total_records": sum(merged_counts.values()),
        "duplicates_removed": total_dupes,
        "extraction_method": "MULTI_SOURCE_MERGED",
        "merged_at": datetime.now(timezone.utc).isoformat(),
    })
