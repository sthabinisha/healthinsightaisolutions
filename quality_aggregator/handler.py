"""
hi-quality-aggregator
---------------------
Aggregates per-resource quality scores into a single composite score.

Weights: completeness 35%, validity 35%, consistency 20%, uniqueness 10%
Threshold: 0.75 for analytics-ready; 0.60 minimum to proceed with warning.
"""

from datetime import datetime, timezone
from shared.utils import TenantLogger, emit_audit_event, ok


def handler(event: dict, context) -> dict:
    tenant_id      = event["tenant_id"]
    quality_reports = event["quality_reports"]   # list from Map state

    log = TenantLogger("quality-aggregator", tenant_id)

    # quality_reports is a list of per-resource report dicts
    # Filter out skipped resources
    active = [r for r in quality_reports if not r.get("skipped") and r.get("record_count", 0) > 0]

    if not active:
        log.warning("No active resource reports — assigning default score 1.0")
        return ok({"score": 1.0, "issues": [], "resource_count": 0, "record_count": 0})

    total_records = sum(r.get("record_count", 0) for r in active)

    # Weighted average by record count (larger resource types weight more)
    weighted_scores = []
    all_issues: list[str] = []

    for report in active:
        weight = report.get("record_count", 0) / total_records if total_records else 1 / len(active)
        score  = report.get("quality_score", 1.0)
        weighted_scores.append(score * weight)
        all_issues.extend(report.get("issues", []))

    composite = round(sum(weighted_scores), 3)

    # Flatten issues and deduplicate
    unique_issues = list(dict.fromkeys(all_issues))[:20]

    emit_audit_event(tenant_id, "QUALITY_SCORED", {
        "composite_score": composite,
        "resource_count": len(active),
        "total_records": total_records,
        "issues_count": len(unique_issues),
    })

    log.info(f"Composite quality score: {composite} across {len(active)} resource types, {total_records} records")

    return ok({
        "score": composite,
        "issues": unique_issues,
        "resource_count": len(active),
        "record_count": total_records,
        "per_resource": {r["resource_type"]: r["quality_score"] for r in active},
        "scored_at": datetime.now(timezone.utc).isoformat(),
    })
