"""
hi-review-escalator
-------------------
Handles SLA timeout for mandatory human review queues.
Escalates to supervisor (CRITICAL) or team lead (HIGH).
"""

import json
from datetime import datetime, timezone
from shared.utils import TenantLogger, get_sns, emit_audit_event, ok, ACCOUNT_ID, REGION


def handler(event: dict, context) -> dict:
    tenant_id       = event["tenant_id"]
    batch_id        = event["batch_id"]
    escalation_level = event.get("escalation_level", "TEAM_LEAD")
    priority        = event.get("priority", "HIGH")

    log = TenantLogger("review-escalator", tenant_id)
    log.error(f"Review SLA EXCEEDED for batch={batch_id} priority={priority}. Escalating to {escalation_level}")

    sns = get_sns()
    topic_arn = f"arn:aws:sns:{REGION}:{ACCOUNT_ID}:hi-escalations-{tenant_id}"

    subject = f"HealthInsight — ESCALATION: {priority} Review SLA Exceeded"
    message = (
        f"ESCALATION REQUIRED\n\n"
        f"Tenant: {tenant_id}\n"
        f"Batch: {batch_id}\n"
        f"Priority: {priority}\n"
        f"Escalation Level: {escalation_level}\n"
        f"Time: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"Items in this batch were not reviewed within the SLA window and have been "
        f"escalated to {escalation_level}. Immediate action required.\n\n"
        f"All items remain in PENDING_ESCALATION status. No automated action has been taken."
    )

    try:
        sns.publish(TopicArn=topic_arn, Subject=subject, Message=message)
    except Exception as exc:
        log.error(f"Escalation SNS publish failed: {exc}")

    emit_audit_event(tenant_id, "REVIEW_ESCALATED", {
        "batch_id": batch_id,
        "priority": priority,
        "escalation_level": escalation_level,
        "sla_exceeded": True,
        "autonomous_action_taken": False,
    })

    return ok({
        "escalated": True,
        "escalation_level": escalation_level,
        "batch_id": batch_id,
        "all_items_status": "PENDING_ESCALATION",
        "escalated_at": datetime.now(timezone.utc).isoformat(),
    })
