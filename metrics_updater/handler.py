"""
hi-metrics-updater
------------------
Updates client dashboard metrics and publishes CloudWatch metrics
for operational monitoring after each pipeline run.
"""

import json
from datetime import datetime, timezone
import boto3, os

from shared.utils import TenantLogger, get_cloudwatch, get_dynamo, emit_audit_event, ok

DASHBOARD_TABLE = os.environ.get("HI_DASHBOARD_TABLE", "hi-dashboard-results")
METRICS_NS      = "HealthInsight/Pipeline"


def _put_metric(cw, tenant_id: str, metric_name: str, value: float, unit: str = "Count") -> None:
    try:
        cw.put_metric_data(
            Namespace=METRICS_NS,
            MetricData=[{
                "MetricName": metric_name,
                "Dimensions": [{"Name": "TenantId", "Value": tenant_id}],
                "Value": value,
                "Unit": unit,
            }],
        )
    except Exception:
        pass  # Non-fatal


def handler(event: dict, context) -> dict:
    tenant_id = event["tenant_id"]
    batch_id  = event["batch_id"]
    metrics   = event.get("metrics", {})

    log = TenantLogger("metrics-updater", tenant_id)
    log.info(f"Updating dashboard metrics for batch={batch_id}")

    cw = get_cloudwatch()

    # Publish each metric to CloudWatch
    metric_map = {
        "ClaimsAnalyzed":       (metrics.get("claims_analyzed", 0), "Count"),
        "HighRiskCount":        (metrics.get("high_risk_count", 0), "Count"),
        "RevenueAtRisk":        (metrics.get("revenue_at_risk", 0), "None"),
        "DataQualityScore":     (metrics.get("quality_score", 0), "None"),
        "PatientsStratified":   (metrics.get("patients_stratified", 0), "Count"),
        "DocumentationGaps":    (metrics.get("documentation_gaps_found", 0), "Count"),
    }

    for name, (value, unit) in metric_map.items():
        _put_metric(cw, tenant_id, name, float(value), unit)

    # Write summary to DynamoDB dashboard table
    dynamo = get_dynamo()
    dynamo.put_item(
        TableName=DASHBOARD_TABLE,
        Item={
            "pk":                    {"S": f"TENANT#{tenant_id}"},
            "sk":                    {"S": f"METRICS#LATEST"},
            "batch_id":              {"S": batch_id},
            "claims_analyzed":       {"N": str(metrics.get("claims_analyzed", 0))},
            "high_risk_count":       {"N": str(metrics.get("high_risk_count", 0))},
            "revenue_at_risk":       {"N": str(metrics.get("revenue_at_risk", 0))},
            "quality_score":         {"N": str(metrics.get("quality_score", 0))},
            "patients_stratified":   {"N": str(metrics.get("patients_stratified", 0))},
            "documentation_gaps":    {"N": str(metrics.get("documentation_gaps_found", 0))},
            "updated_at":            {"S": datetime.now(timezone.utc).isoformat()},
            "ttl":                   {"N": str(int(__import__("time").time()) + 90 * 24 * 3600)},
        }
    )

    log.info("Dashboard metrics updated")
    return ok({
        "metrics_updated": True,
        "metrics_published": list(metric_map.keys()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
