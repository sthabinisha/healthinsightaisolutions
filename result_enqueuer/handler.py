"""
hi-result-enqueuer
------------------
Enqueues merged results to the appropriate SQS review queue with
priority metadata. Used before MandatoryHumanReview states.
"""

import json
from datetime import datetime, timezone
import boto3, os

from shared.utils import TenantLogger, get_sqs, emit_audit_event, ok, ACCOUNT_ID, REGION


def handler(event: dict, context) -> dict:
    tenant_id  = event["tenant_id"]
    batch_id   = event["batch_id"]
    results    = event["results"]
    priority   = event.get("priority", "HIGH")
    sla_hours  = event.get("sla_hours", 24)
    escalation = event.get("escalation", False)

    log = TenantLogger("result-enqueuer", tenant_id)
    log.info(f"Enqueuing {priority} results for human review. sla={sla_hours}h")

    sqs = get_sqs()
    queue_suffix = "critical" if priority == "CRITICAL" else "standard"
    queue_url = f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT_ID}/hi-review-{queue_suffix}-{tenant_id}"

    items = results.get("critical_risk_items" if priority == "CRITICAL" else "high_risk_items", [])

    message = {
        "batch_id": batch_id,
        "tenant_id": tenant_id,
        "priority": priority,
        "sla_hours": sla_hours,
        "items": items[:20],     # Limit payload; full results in S3
        "item_count": len(items),
        "total_revenue_at_risk": results.get("total_revenue_at_risk", 0),
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "escalation_required": escalation,
    }

    try:
        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message, default=str),
            MessageAttributes={
                "priority": {"DataType": "String", "StringValue": priority},
                "tenant_id": {"DataType": "String", "StringValue": tenant_id},
                "sla_hours": {"DataType": "Number", "StringValue": str(sla_hours)},
            },
        )
        log.info(f"Enqueued {len(items)} {priority} items to review queue")
    except Exception as exc:
        log.error(f"SQS send failed: {exc}")
        raise

    emit_audit_event(tenant_id, "RESULTS_ENQUEUED_FOR_REVIEW", {
        "batch_id": batch_id,
        "priority": priority,
        "item_count": len(items),
        "queue": queue_suffix,
        "sla_hours": sla_hours,
    })

    return ok({
        "enqueued": True,
        "priority": priority,
        "item_count": len(items),
        "queue": queue_url,
        "sla_hours": sla_hours,
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
    })
