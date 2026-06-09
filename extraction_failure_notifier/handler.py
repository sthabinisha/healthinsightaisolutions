"""
hi-extraction-failure-notifier
-------------------------------
All extraction strategies failed. Notifies tenant admin with specific
error details and remediation steps.
"""

from datetime import datetime, timezone
from shared.utils import TenantLogger, get_sns, emit_audit_event, ok, ACCOUNT_ID, REGION


def handler(event: dict, context) -> dict:
    tenant_id        = event["tenant_id"]
    batch_id         = event["batch_id"]
    error            = event.get("error", {})
    remediation_steps = event.get("remediation_steps", [])

    log = TenantLogger("extraction-failure-notifier", tenant_id)
    log.error(f"All extraction strategies failed for batch={batch_id}. Error: {error}")

    sns = get_sns()
    topic_arn = f"arn:aws:sns:{REGION}:{ACCOUNT_ID}:hi-notifications-{tenant_id}"

    subject = "HealthInsight — ACTION REQUIRED: Data Extraction Failed"
    steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(remediation_steps))
    body = (
        f"HealthInsight was unable to extract data for your account.\n\n"
        f"Batch ID: {batch_id}\n"
        f"Failed At: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Error: {error.get('Cause', str(error))[:500]}\n\n"
        f"Remediation Steps:\n{steps_text}\n\n"
        f"Please contact HealthInsight support if all steps have been verified and "
        f"the issue persists. No data was processed or stored for this batch."
    )

    try:
        sns.publish(TopicArn=topic_arn, Subject=subject, Message=body)
    except Exception as exc:
        log.error(f"SNS publish failed: {exc}")

    emit_audit_event(tenant_id, "EXTRACTION_FAILED", {
        "batch_id": batch_id,
        "error": str(error)[:500],
        "notification_sent": True,
    })

    return ok({
        "notification_sent": True,
        "batch_id": batch_id,
        "failed_at": datetime.now(timezone.utc).isoformat(),
    })
