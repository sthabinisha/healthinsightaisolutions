"""
hi-flatfile-extractor
---------------------
Processes CSV, pipe-delimited, or fixed-width healthcare data exports
uploaded to the tenant S3 ingestion bucket.

Validates column headers, maps to FHIR resource fields, enforces data
types per the tenant column mapping config, and writes NDJSON to the
S3 raw zone.
"""

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from shared.utils import (
    TenantLogger, get_s3, emit_audit_event,
    ok, RAW_BUCKET,
)


# ---------------------------------------------------------------------------
# Delimiter resolver
# ---------------------------------------------------------------------------

DELIMITERS = {
    "csv": ",",
    "pipe": "|",
    "tab": "\t",
    "semicolon": ";",
}


def _resolve_delimiter(file_format: str) -> str:
    return DELIMITERS.get(file_format.lower(), ",")


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------

def _coerce(value: str, dtype: str) -> Any:
    """Coerce a raw string to the expected Python type."""
    value = value.strip()
    if not value:
        return None
    if dtype == "string":
        return value
    if dtype == "integer":
        return int(value)
    if dtype == "float":
        return float(value)
    if dtype == "boolean":
        return value.lower() in ("true", "yes", "1", "y")
    if dtype == "date":
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%d-%b-%Y"):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue
    return value


# ---------------------------------------------------------------------------
# Column mapping → FHIR record builder
# ---------------------------------------------------------------------------

def _build_record(row: dict, column_mapping: list[dict], resource_type: str, tenant_id: str) -> dict:
    """
    Apply column_mapping to a CSV row and produce a FHIR-ish record.

    column_mapping entry format:
      {"csv_column": "PatientID", "fhir_path": "id", "type": "string", "required": true}
    """
    record: dict = {
        "resourceType": resource_type,
        "_source": "FLAT_FILE",
        "tenant_id": tenant_id,
        "_extracted_at": datetime.now(timezone.utc).isoformat(),
    }
    missing_required = []

    for mapping in column_mapping:
        csv_col   = mapping["csv_column"]
        fhir_path = mapping["fhir_path"]
        dtype     = mapping.get("type", "string")
        required  = mapping.get("required", False)

        raw_val = row.get(csv_col)
        if raw_val is None or raw_val.strip() == "":
            if required:
                missing_required.append(csv_col)
            continue

        coerced = _coerce(raw_val, dtype)
        # Support dotted fhir_path like "name.0.family"
        parts = fhir_path.split(".")
        target = record
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = coerced

    record["_missing_required"] = missing_required
    return record


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    tenant_id      = event["tenant_id"]
    input_bucket   = event["s3_input_bucket"]
    input_prefix   = event["s3_input_prefix"]
    file_format    = event.get("file_format", "csv")
    column_mapping = event.get("column_mapping", [])
    resource_type  = event.get("resource_type", "Patient")
    s3_raw_prefix  = event.get("s3_raw_prefix", f"raw/{tenant_id}/flatfile/")

    log = TenantLogger("flatfile-extractor", tenant_id)
    log.info(f"Flat-file extraction from s3://{input_bucket}/{input_prefix}")

    s3 = get_s3()
    delimiter = _resolve_delimiter(file_format)

    # List files in the ingestion prefix
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=input_bucket, Prefix=input_prefix):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith("/"):
                keys.append(obj["Key"])

    if not keys:
        raise FileNotFoundError(f"No files found at s3://{input_bucket}/{input_prefix}")

    log.info(f"Found {len(keys)} files to process")

    all_records = []
    total_rows = 0
    validation_errors = 0
    header_validated = False
    expected_headers: set | None = None

    for key in keys:
        log.info(f"Processing {key}")
        obj = s3.get_object(Bucket=input_bucket, Key=key)
        content = obj["Body"].read().decode("utf-8-sig")  # strip BOM

        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)

        # Validate headers on first file
        if not header_validated and column_mapping:
            expected = {m["csv_column"] for m in column_mapping if m.get("required")}
            actual = set(reader.fieldnames or [])
            missing = expected - actual
            if missing:
                raise ValueError(f"Required columns missing in {key}: {missing}")
            expected_headers = actual
            header_validated = True

        for row in reader:
            total_rows += 1
            try:
                record = _build_record(row, column_mapping, resource_type, tenant_id)
                if record.get("_missing_required"):
                    validation_errors += 1
                    log.warning(f"Row {total_rows} missing required fields: {record['_missing_required']}")
                all_records.append(record)
            except Exception as exc:
                validation_errors += 1
                log.warning(f"Row {total_rows} parse error: {exc}")

    log.info(f"Parsed {total_rows} rows, {validation_errors} validation errors")

    # Write NDJSON to raw zone
    out_key = f"{s3_raw_prefix}{resource_type}.ndjson"
    ndjson = "\n".join(json.dumps(r, default=str) for r in all_records)
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=out_key,
        Body=ndjson.encode(),
        ContentType="application/fhir+ndjson",
        ServerSideEncryption="aws:kms",
        Metadata={"tenant_id": tenant_id, "source_bucket": input_bucket},
    )

    emit_audit_event(tenant_id, "FLATFILE_EXTRACTION_COMPLETE", {
        "files_processed": len(keys),
        "total_rows": total_rows,
        "validation_errors": validation_errors,
        "output_key": out_key,
    })

    return ok({
        "s3_location": f"s3://{RAW_BUCKET}/{s3_raw_prefix}",
        "resource_types_extracted": [resource_type],
        "record_counts": {resource_type: len(all_records)},
        "total_records": len(all_records),
        "validation_errors": validation_errors,
        "extraction_method": "FLAT_FILE",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    })
