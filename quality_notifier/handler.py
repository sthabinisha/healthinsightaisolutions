"""
hi-quality-notifier
-------------------
Sends quality warning or remediation-required notification to the
tenant admin. Used by both SendQualityWarningAndProceed and
NotifyQualityRemediationRequired states.
"""

import json
import os
from datetime import datetime, timezone

import boto3

from shared.utils import TenantLogger, get_sns, emit_audit_event, ok, ACCOUNT_ID, REGION


def handler(event: dict, context) -> dict:
    tenant_id         = event["tenant_id"]
    quality_score     = event["quality_score"]
    issues            = event.get("issues", [])
    notification_type = event.get("notification_type", "QUALITY_WARNING")
    proceed           = event.get("proceed", True)

    log = TenantLogger("quality-notifier", tenant_id)
    log.info(f"Sending {notification_type} notification. score={quality_score}, proceed={proceed}")

    sns = get_sns()
    topic_arn = f"arn:aws:sns:{REGION}:{ACCOUNT_ID}:hi-notifications-{tenant_id}"

    if notification_type == "QUALITY_WARNING":
        subject = "HealthInsight — Data Quality Warning: Processing Proceeding with Caution"
        body = (
            f"Data quality score: {quality_score:.2f}/1.00 (below preferred threshold of 0.75).\n"
            f"Processing will continue, but analytics reliability may be reduced.\n\n"
            f"Issues identified:\n" + "\n".join(f"  • {i}" for i in issues[:10]) +
            f"\n\nPlease address these issues before the next batch for best results.\n"
            f"Batch processed: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
    else:  # REMEDIATION_REQUIRED
        subject = "HealthInsight — ACTION REQUIRED: Data Quality Below Minimum Threshold"
        body = (
            f"Data quality score: {quality_score:.2f}/1.00 (below minimum threshold of 0.60).\n"
            f"Analytics processing CANNOT proceed until data quality is remediated.\n\n"
            f"Issues requiring remediation:\n" + "\n".join(f"  • {i}" for i in issues[:10]) +
            f"\n\nPlease contact your HealthInsight implementation team for remediation guidance.\n"
            f"Batch rejected: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

    try:
        sns.publish(TopicArn=topic_arn, Subject=subject, Message=body)
        log.info("Quality notification sent via SNS")
    except Exception as exc:
        log.error(f"SNS publish failed: {exc}")

    emit_audit_event(tenant_id, "QUALITY_NOTIFICATION_SENT", {
        "notification_type": notification_type,
        "quality_score": quality_score,
        "proceed": proceed,
        "issue_count": len(issues),
    })

    return ok({
        "notification_type": notification_type,
        "notification_sent": True,
        "quality_score": quality_score,
        "proceed": proceed,
        "notified_at": datetime.now(timezone.utc).isoformat(),
    })
