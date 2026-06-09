"""
hi-rejection-logger
-------------------
Records reviewer rejections with reasons.
Feeds rejected examples back into model improvement pipeline
for future Strands agent fine-tuning.
"""

import json
from datetime import datetime, timezone
from shared.utils import TenantLogger, get_s3, emit_audit_event, ok, PROC_BUCKET


def handler(event: dict, context) -> dict:
    tenant_id       = event["tenant_id"]
    batch_id        = event["batch_id"]
    rejected_items  = event.get("rejected_items", [])
    rejection_reasons = event.get("rejection_reasons", [])
    reviewer_id     = event.get("reviewer_id", "UNKNOWN")

    log = TenantLogger("rejection-logger", tenant_id)
    log.info(f"Logging {len(rejected_items)} rejections from reviewer={reviewer_id}")

    s3 = get_s3()
    feedback_records = []

    for i, item in enumerate(rejected_items):
        reason = rejection_reasons[i] if i < len(rejection_reasons) else "No reason provided"
        record = {
            "item_id":        item.get("item_id", f"item-{i}"),
            "item_type":      item.get("item_type", ""),
            "ai_recommendation": item.get("ai_recommendation", ""),
            "rejection_reason": reason,
            "reviewer_id":    reviewer_id,
            "batch_id":       batch_id,
            "rejected_at":    datetime.now(timezone.utc).isoformat(),
            "feedback_type":  "REJECTION",
            "used_for_training": False,  # Set to True after compliance review
        }
        feedback_records.append(record)

    if feedback_records:
        key = f"model-feedback/{tenant_id}/{batch_id}/rejections.ndjson"
        ndjson = "\n".join(json.dumps(r, default=str) for r in feedback_records)
        s3.put_object(
            Bucket=PROC_BUCKET, Key=key, Body=ndjson.encode(),
            ContentType="application/json", ServerSideEncryption="aws:kms",
            Metadata={"tenant_id": tenant_id, "feedback_type": "rejection"},
        )

    emit_audit_event(tenant_id, "REJECTIONS_LOGGED", {
        "batch_id": batch_id,
        "rejection_count": len(rejected_items),
        "reviewer_id": reviewer_id,
    })

    return ok({
        "logged_count": len(feedback_records),
        "batch_id": batch_id,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    })
