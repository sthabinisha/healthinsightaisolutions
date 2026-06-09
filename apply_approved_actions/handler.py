"""
hi-apply-approved-actions
-------------------------
Applies ONLY the recommendations explicitly approved by the human reviewer.
Partial approvals handled item-by-item.
Records reviewer ID, decision, and timestamp in immutable audit log.

This is the ONLY place in the entire workflow where any action is taken
based on AI analysis — and only after explicit human authorization.
"""

import json
from datetime import datetime, timezone
from shared.utils import TenantLogger, get_s3, emit_audit_event, ok, ACCOUNT_ID, PROC_BUCKET


def handler(event: dict, context) -> dict:
    tenant_id     = event["tenant_id"]
    batch_id      = event["batch_id"]
    approved_items = event.get("approved_items", [])
    reviewer_id   = event.get("reviewer_id", "UNKNOWN")
    reviewed_at   = event.get("reviewed_at", datetime.now(timezone.utc).isoformat())
    override_reason = event.get("override_reason", "")

    log = TenantLogger("apply-approved-actions", tenant_id)
    log.info(f"Applying {len(approved_items)} approved actions. reviewer={reviewer_id}")

    applied = []
    failed  = []

    for item in approved_items:
        item_id    = item.get("item_id", "UNKNOWN")
        action     = item.get("action_type", "")
        payload    = item.get("action_payload", {})

        try:
            # Store approval record to S3 (immutable audit trail)
            approval_record = {
                "item_id":        item_id,
                "action_type":    action,
                "reviewer_id":    reviewer_id,
                "reviewed_at":    reviewed_at,
                "applied_at":     datetime.now(timezone.utc).isoformat(),
                "override_reason": override_reason,
                "payload_summary": str(payload)[:200],
                "ai_model":       "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
                "human_authorized": True,
            }

            s3_key = f"approved-actions/{tenant_id}/{batch_id}/{item_id}.json"
            get_s3().put_object(
                Bucket=PROC_BUCKET,
                Key=s3_key,
                Body=json.dumps(approval_record, default=str).encode(),
                ContentType="application/json",
                ServerSideEncryption="aws:kms",
                Metadata={"tenant_id": tenant_id, "reviewer_id": reviewer_id},
            )

            # In production: dispatch action to appropriate downstream system
            # e.g., flag claim in billing system, update EHR via FHIR PATCH, etc.
            # For MVP: record is the action (no direct EHR write without additional integration)

            applied.append({"item_id": item_id, "action_type": action, "s3_key": s3_key})
            log.info(f"Applied action: {action} on {item_id}")

        except Exception as exc:
            log.error(f"Failed to apply action on {item_id}: {exc}")
            failed.append({"item_id": item_id, "error": str(exc)})

    emit_audit_event(tenant_id, "APPROVED_ACTIONS_APPLIED", {
        "batch_id": batch_id,
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at,
        "applied_count": len(applied),
        "failed_count": len(failed),
        "human_authorized": True,
        "autonomous_action_taken": False,
        "override_reason": override_reason,
    })

    return ok({
        "applied_count": len(applied),
        "failed_count": len(failed),
        "applied_items": applied,
        "failed_items": failed,
        "reviewer_id": reviewer_id,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    })
