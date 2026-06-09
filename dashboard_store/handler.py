"""
hi-dashboard-store
------------------
Stores medium and low risk results in the client dashboard.
Generates dashboard alerts for medium risk items.
No workflow pause for medium/low — results surface to dashboard UI.
"""

import json
from datetime import datetime, timezone
import boto3, os

from shared.utils import TenantLogger, get_s3, get_dynamo, emit_audit_event, ok, ACCOUNT_ID, REGION

DASHBOARD_TABLE = os.environ.get("HI_DASHBOARD_TABLE", "hi-dashboard-results")


def handler(event: dict, context) -> dict:
    tenant_id  = event["tenant_id"]
    results    = event["results"]
    priority   = event.get("priority", "MEDIUM")
    alert_type = event.get("alert_type", "DASHBOARD_WARNING")

    log = TenantLogger("dashboard-store", tenant_id)
    log.info(f"Storing {priority} results to dashboard. alert_type={alert_type}")

    dynamo = get_dynamo()
    batch_id = results.get("batch_id", "unknown")

    item = {
        "pk":             {"S": f"TENANT#{tenant_id}"},
        "sk":             {"S": f"BATCH#{batch_id}#{priority}"},
        "tenant_id":      {"S": tenant_id},
        "batch_id":       {"S": batch_id},
        "priority":       {"S": priority},
        "alert_type":     {"S": alert_type},
        "claim_count":    {"N": str(results.get("total_claims", 0))},
        "high_risk_count":{"N": str(results.get("high_risk_count", 0))},
        "revenue_at_risk":{"N": str(results.get("total_revenue_at_risk", 0))},
        "stored_at":      {"S": datetime.now(timezone.utc).isoformat()},
        "requires_action":{"BOOL": priority in ("MEDIUM",)},
        "ttl":            {"N": str(int(__import__("time").time()) + 90 * 24 * 3600)},  # 90-day dashboard retention
    }

    dynamo.put_item(TableName=DASHBOARD_TABLE, Item=item)

    emit_audit_event(tenant_id, "DASHBOARD_UPDATED", {
        "batch_id": batch_id,
        "priority": priority,
        "alert_type": alert_type,
    })

    log.info(f"Dashboard updated for {priority} results")
    return ok({
        "stored": True,
        "priority": priority,
        "alert_type": alert_type,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    })
