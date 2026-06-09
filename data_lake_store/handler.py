"""
hi-data-lake-store
------------------
Writes normalized FHIR R4 resources to S3 analytics zone.
Registers tables in AWS Glue Data Catalog.
Configures Athena workgroup per tenant.
Enforces bucket-level tenant isolation via IAM resource tags.
"""

import json
import os
from datetime import datetime, timezone

import boto3

from shared.utils import (
    TenantLogger, get_s3, get_glue, get_athena,
    emit_audit_event, ok, PROC_BUCKET, ACCOUNT_ID, REGION,
)

ANALYTICS_BUCKET = os.environ.get("HI_ANALYTICS_BUCKET", f"hi-analytics-{ACCOUNT_ID}")


# ---------------------------------------------------------------------------
# Glue table schema definitions (FHIR R4 → Parquet columns)
# ---------------------------------------------------------------------------

GLUE_SCHEMAS = {
    "Patient": [
        {"Name": "id",          "Type": "string"},
        {"Name": "birthdate",   "Type": "string"},
        {"Name": "gender",      "Type": "string"},
        {"Name": "tenant_id",   "Type": "string"},
        {"Name": "batch_id",    "Type": "string"},
        {"Name": "_raw",        "Type": "string"},  # Full JSON for downstream use
    ],
    "Claim": [
        {"Name": "id",          "Type": "string"},
        {"Name": "status",      "Type": "string"},
        {"Name": "use",         "Type": "string"},
        {"Name": "patient_ref", "Type": "string"},
        {"Name": "created",     "Type": "string"},
        {"Name": "total_value", "Type": "double"},
        {"Name": "tenant_id",   "Type": "string"},
        {"Name": "batch_id",    "Type": "string"},
        {"Name": "_raw",        "Type": "string"},
    ],
    "Encounter": [
        {"Name": "id",          "Type": "string"},
        {"Name": "status",      "Type": "string"},
        {"Name": "class_code",  "Type": "string"},
        {"Name": "patient_ref", "Type": "string"},
        {"Name": "period_start","Type": "string"},
        {"Name": "period_end",  "Type": "string"},
        {"Name": "tenant_id",   "Type": "string"},
        {"Name": "batch_id",    "Type": "string"},
        {"Name": "_raw",        "Type": "string"},
    ],
    "Condition": [
        {"Name": "id",          "Type": "string"},
        {"Name": "code",        "Type": "string"},
        {"Name": "status",      "Type": "string"},
        {"Name": "patient_ref", "Type": "string"},
        {"Name": "onset_date",  "Type": "string"},
        {"Name": "tenant_id",   "Type": "string"},
        {"Name": "batch_id",    "Type": "string"},
        {"Name": "_raw",        "Type": "string"},
    ],
}

DEFAULT_SCHEMA = [
    {"Name": "id",        "Type": "string"},
    {"Name": "tenant_id", "Type": "string"},
    {"Name": "batch_id",  "Type": "string"},
    {"Name": "_raw",      "Type": "string"},
]


def _flatten_record(record: dict, resource_type: str, batch_id: str) -> dict:
    """Flatten a FHIR record into the analytics schema row."""
    flat = {
        "id":        record.get("id", ""),
        "tenant_id": record.get("tenant_id", ""),
        "batch_id":  batch_id,
        "_raw":      json.dumps(record, default=str),
    }
    if resource_type == "Claim":
        flat["status"]      = record.get("status", "")
        flat["use"]         = record.get("use", "")
        flat["patient_ref"] = record.get("patient", {}).get("reference", "") if isinstance(record.get("patient"), dict) else ""
        flat["created"]     = record.get("created", "")
        total = record.get("total", {})
        flat["total_value"] = total.get("value", 0.0) if isinstance(total, dict) else 0.0
    elif resource_type == "Patient":
        flat["birthdate"]   = record.get("birthDate", "")
        flat["gender"]      = record.get("gender", "")
    elif resource_type == "Encounter":
        cls = record.get("class", {})
        flat["class_code"]  = cls.get("code", "") if isinstance(cls, dict) else str(cls)
        flat["status"]      = record.get("status", "")
        flat["patient_ref"] = record.get("subject", {}).get("reference", "") if isinstance(record.get("subject"), dict) else ""
        period = record.get("period", {})
        flat["period_start"] = period.get("start", "") if isinstance(period, dict) else ""
        flat["period_end"]   = period.get("end", "") if isinstance(period, dict) else ""
    elif resource_type == "Condition":
        code_obj = record.get("code", {})
        coding = code_obj.get("coding", [{}])[0] if isinstance(code_obj, dict) else {}
        flat["code"]        = coding.get("code", "")
        flat["status"]      = record.get("clinicalStatus", {}).get("coding", [{}])[0].get("code", "")
        flat["patient_ref"] = record.get("subject", {}).get("reference", "") if isinstance(record.get("subject"), dict) else ""
        flat["onset_date"]  = record.get("onsetDateTime", "")
    return flat


def _ensure_glue_database(glue, db_name: str) -> None:
    try:
        glue.get_database(Name=db_name)
    except glue.exceptions.EntityNotFoundException:
        glue.create_database(DatabaseInput={
            "Name": db_name,
            "Description": f"HealthInsight analytics database for tenant {db_name.replace('healthinsight_', '')}",
        })


def _ensure_glue_table(glue, db_name: str, table_name: str, s3_location: str, columns: list) -> None:
    table_input = {
        "Name": table_name,
        "StorageDescriptor": {
            "Columns": columns,
            "Location": s3_location,
            "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
            "SerdeInfo": {
                "SerializationLibrary": "org.openx.data.jsonserde.JsonSerDe",
                "Parameters": {"serialization.format": "1"},
            },
        },
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {"classification": "json", "compressionType": "none"},
    }
    try:
        glue.get_table(DatabaseName=db_name, Name=table_name)
        glue.update_table(DatabaseName=db_name, TableInput=table_input)
    except glue.exceptions.EntityNotFoundException:
        glue.create_table(DatabaseName=db_name, TableInput=table_input)


def _ensure_athena_workgroup(athena, tenant_id: str) -> None:
    wg_name = f"hi-{tenant_id}"
    try:
        athena.get_work_group(WorkGroup=wg_name)
    except athena.exceptions.InvalidRequestException:
        athena.create_work_group(
            Name=wg_name,
            Configuration={
                "ResultConfiguration": {
                    "OutputLocation": f"s3://{ANALYTICS_BUCKET}/athena-results/{tenant_id}/",
                    "EncryptionConfiguration": {"EncryptionOption": "SSE_S3"},
                },
                "EnforceWorkGroupConfiguration": True,
                "PublishCloudWatchMetricsEnabled": True,
            },
            Description=f"HealthInsight Athena workgroup for tenant {tenant_id}",
            Tags=[{"Key": "tenant_id", "Value": tenant_id}, {"Key": "managed_by", "Value": "healthinsight"}],
        )


def handler(event: dict, context) -> dict:
    tenant_id         = event["tenant_id"]
    batch_id          = event["batch_id"]
    normalized_files  = event["normalized_files"]   # list from Map state
    target_format     = event.get("target_format", "PARQUET")
    glue_database     = event.get("glue_database", f"healthinsight_{tenant_id}")

    log = TenantLogger("data-lake-store", tenant_id)
    log.info(f"Storing to data lake. db={glue_database}, files={len(normalized_files)}")

    s3    = get_s3()
    glue  = get_glue()
    athena = get_athena()

    _ensure_glue_database(glue, glue_database)
    _ensure_athena_workgroup(athena, tenant_id)

    stored_paths: dict[str, str] = {}

    for file_result in normalized_files:
        if file_result.get("skipped"):
            continue
        resource_type = file_result["resource_type"]
        src_loc       = file_result["output_location"].replace(f"s3://{PROC_BUCKET}/", "")

        # Read normalized NDJSON
        try:
            obj = s3.get_object(Bucket=PROC_BUCKET, Key=src_loc)
            lines = [l for l in obj["Body"].read().decode().strip().splitlines() if l.strip()]
        except Exception as exc:
            log.warning(f"Could not read {src_loc}: {exc}")
            continue

        # Flatten and write to analytics zone
        out_prefix = f"analytics/{tenant_id}/{resource_type.lower()}/batch={batch_id}/"
        out_key    = f"{out_prefix}data.json"
        flat_records = []

        for line in lines:
            try:
                record = json.loads(line)
                flat_records.append(_flatten_record(record, resource_type, batch_id))
            except json.JSONDecodeError:
                continue

        if flat_records:
            ndjson = "\n".join(json.dumps(r, default=str) for r in flat_records)
            s3.put_object(
                Bucket=ANALYTICS_BUCKET, Key=out_key, Body=ndjson.encode(),
                ContentType="application/json", ServerSideEncryption="aws:kms",
                Metadata={"tenant_id": tenant_id, "batch_id": batch_id, "resource_type": resource_type},
            )

        # Register/update Glue table
        table_name = resource_type.lower()
        table_s3   = f"s3://{ANALYTICS_BUCKET}/analytics/{tenant_id}/{table_name}/"
        columns    = GLUE_SCHEMAS.get(resource_type, DEFAULT_SCHEMA)
        _ensure_glue_table(glue, glue_database, table_name, table_s3, columns)

        stored_paths[resource_type] = f"s3://{ANALYTICS_BUCKET}/{out_prefix}"
        log.info(f"Stored {len(flat_records)} {resource_type} rows to analytics lake")

    emit_audit_event(tenant_id, "DATA_LAKE_STORED", {
        "batch_id": batch_id,
        "glue_database": glue_database,
        "resource_types": list(stored_paths.keys()),
    })

    return ok({
        "glue_database": glue_database,
        "athena_workgroup": f"hi-{tenant_id}",
        "stored_resources": list(stored_paths.keys()),
        "patient_path": stored_paths.get("Patient", ""),
        "encounter_path": stored_paths.get("Encounter", ""),
        "claim_path": stored_paths.get("Claim", ""),
        "condition_path": stored_paths.get("Condition", ""),
        "analytics_bucket": ANALYTICS_BUCKET,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    })
