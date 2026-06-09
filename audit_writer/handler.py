"""
hi-audit-writer
---------------
Writes complete structured audit trail to CloudWatch Logs with
7-year retention (HIPAA Security Rule 164.312(b)).

Records:
  - Who extracted what PHI
  - Which AI model processed it
  - Who reviewed the output
  - What decision was made
  - When each step occurred
"""

import json
import time
from datetime import datetime, timezone

import boto3

from shared.utils import TenantLogger, get_logs, get_s3, emit_audit_event, ok, AUDIT_GROUP, PROC_BUCKET, ACCOUNT_ID

AUDIT_RETENTION_DAYS = 2555  # 7 years


def _ensure_log_group(logs, group: str) -> None:
    try:
        logs.create_log_group(logGroupName=group)
        logs.put_retention_policy(logGroupName=group, retentionInDays=AUDIT_RETENTION_DAYS)
    except logs.exceptions.ResourceAlreadyExistsException:
        pass


def handler(event: dict, context) -> dict:
    tenant_id   = event["tenant_id"]
    batch_id    = event["batch_id"]
    workflow_id = event["workflow_id"]

    log = TenantLogger("audit-writer", tenant_id)
    log.info(f"Writing audit trail for workflow={workflow_id}")

    logs_client = get_logs()
    _ensure_log_group(logs_client, AUDIT_GROUP)

    log_stream = f"{tenant_id}/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{batch_id}"
    try:
        logs_client.create_log_stream(logGroupName=AUDIT_GROUP, logStreamName=log_stream)
    except logs_client.exceptions.ResourceAlreadyExistsException:
        pass

    audit_record = {
        "audit_version":    "1.0",
        "workflow_id":      workflow_id,
        "batch_id":         batch_id,
        "tenant_id":        tenant_id,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "phi_access": {
            "phi_accessed":       True,
            "phi_classified":     event.get("audit_events", {}).get("phi_classified", False),
            "minimum_necessary":  True,
            "access_log_ref":     event.get("phi_access_log", {}),
        },
        "ai_usage": event.get("ai_usage", {}),
        "pipeline_events": event.get("audit_events", {}),
        "hipaa_rule_ref":   "45 CFR §164.312(b) — Audit Controls",
        "retention_policy": f"{AUDIT_RETENTION_DAYS} days (7 years)",
    }

    logs_client.put_log_events(
        logGroupName=AUDIT_GROUP,
        logStreamName=log_stream,
        logEvents=[{
            "timestamp": int(time.time() * 1000),
            "message": json.dumps(audit_record, default=str),
        }],
    )

    # Also write to S3 for long-term archival (Glacier after 90 days via lifecycle policy)
    s3_key = f"audit-archive/{tenant_id}/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}/{batch_id}.json"
    get_s3().put_object(
        Bucket=PROC_BUCKET,
        Key=s3_key,
        Body=json.dumps(audit_record, default=str).encode(),
        ContentType="application/json",
        ServerSideEncryption="aws:kms",
        Metadata={"tenant_id": tenant_id, "batch_id": batch_id, "retention": "7yr"},
    )

    log.info(f"Audit trail written to CloudWatch and S3. stream={log_stream}")
    return ok({
        "audit_written": True,
        "log_group": AUDIT_GROUP,
        "log_stream": log_stream,
        "s3_archive_key": s3_key,
        "retention_days": AUDIT_RETENTION_DAYS,
        "written_at": datetime.now(timezone.utc).isoformat(),
    })
