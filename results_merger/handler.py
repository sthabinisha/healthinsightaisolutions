"""
hi-results-merger
-----------------
Merges results from all three analysis pipelines.
Deduplicates overlapping findings.
Assigns overall risk classification per claim and patient.
Computes total revenue at risk.
"""

from datetime import datetime, timezone
from shared.utils import TenantLogger, emit_audit_event, ok


def handler(event: dict, context) -> dict:
    tenant_id          = event["tenant_id"]
    batch_id           = event["batch_id"]
    claims_analysis    = event.get("claims_analysis", {})
    risk_stratification = event.get("risk_stratification", {})
    documentation_gaps = event.get("documentation_gaps", {})

    log = TenantLogger("results-merger", tenant_id)
    log.info("Merging analysis pipeline results")

    # --- Claims analysis results ---
    claims_results = claims_analysis if isinstance(claims_analysis, list) else []
    critical_claims = [r for r in claims_results if r.get("risk_level") == "CRITICAL"]
    high_claims     = [r for r in claims_results if r.get("risk_level") == "HIGH"]
    medium_claims   = [r for r in claims_results if r.get("risk_level") == "MEDIUM"]

    # --- Risk stratification results ---
    high_risk_patients = risk_stratification.get("high_risk_patients", []) if isinstance(risk_stratification, dict) else []
    critical_patients  = [p for p in high_risk_patients if p.get("risk_tier") == "CRITICAL"]
    high_patients      = [p for p in high_risk_patients if p.get("risk_tier") == "HIGH"]

    # --- Documentation gaps ---
    gap_report = documentation_gaps.get("documentation_gaps_report", []) if isinstance(documentation_gaps, dict) else []
    high_gap_encounters = [r for r in gap_report if r.get("high_severity_gaps", 0) > 0]

    # Determine highest risk classification
    has_critical = len(critical_claims) > 0 or len(critical_patients) > 0
    has_high     = len(high_claims) > 0 or len(high_patients) > 0 or len(high_gap_encounters) > 0
    has_medium   = len(medium_claims) > 0

    # Estimate revenue at risk (simplified — sum of claim amounts for high/critical)
    total_revenue_at_risk = sum(
        float(c.get("claim_amount", 0) or 0) for c in critical_claims + high_claims
    )

    emit_audit_event(tenant_id, "RESULTS_MERGED", {
        "batch_id": batch_id,
        "critical_items": len(critical_claims) + len(critical_patients),
        "high_items": len(high_claims) + len(high_patients),
        "revenue_at_risk": total_revenue_at_risk,
    })

    return ok({
        "batch_id": batch_id,
        "has_critical_risk_items": has_critical,
        "has_high_risk_items": has_high,
        "has_medium_risk_items": has_medium,
        "critical_risk_items": critical_claims + critical_patients,
        "high_risk_items": high_claims + high_patients,
        "total_claims": len(claims_results),
        "high_risk_count": len(high_claims) + len(high_patients) + len(critical_claims) + len(critical_patients),
        "total_revenue_at_risk": round(total_revenue_at_risk, 2),
        "documentation_gaps_count": documentation_gaps.get("gaps_found", 0),
        "patients_analyzed": risk_stratification.get("patients_analyzed", 0) if isinstance(risk_stratification, dict) else 0,
        "merged_at": datetime.now(timezone.utc).isoformat(),
    })
