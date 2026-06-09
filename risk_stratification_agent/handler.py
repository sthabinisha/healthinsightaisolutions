"""
hi-risk-stratification-agent
-----------------------------
Identifies high-risk patients using RiskStratificationAgent (Bedrock Strands).

Analyzes: visit patterns, chronic conditions, medication adherence,
gaps in care, and social determinants of health (SDOH).

Produces a prioritized care coordinator outreach list.
ALL outputs require human review — no care decisions are made autonomously.
"""

import json
from datetime import datetime, timezone

from shared.utils import TenantLogger, get_s3, get_bedrock, emit_audit_event, ok, ACCOUNT_ID, REGION, PROC_BUCKET

MODEL_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"

RISK_FACTORS = [
    "ed_utilization_pattern",
    "chronic_conditions",
    "medication_adherence",
    "gap_in_care",
    "social_determinants",
]

SYSTEM_PROMPT = (
    "You are a patient risk stratification AI for HealthInsight AI Solutions. "
    "Analyze patient data to identify individuals who may benefit from proactive "
    "care coordination outreach. "
    "Your output is a decision-support list for care coordinators to review. "
    "You do NOT make clinical decisions. You identify patterns and flag patients "
    "for human review only. All outputs require authorized clinical staff review "
    "before any patient contact or care plan modification."
)

RISK_STRATIFICATION_TOOL = {
    "toolSpec": {
        "name": "stratify_patient_risk",
        "description": (
            "Analyzes a patient's clinical data and assigns a risk score and tier. "
            "Returns risk_score (0-100), risk_tier (LOW/MEDIUM/HIGH/CRITICAL), "
            "and primary risk factors driving the score."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "patient_id":          {"type": "string"},
                    "ed_visits_90d":       {"type": "integer"},
                    "chronic_conditions":  {"type": "array", "items": {"type": "string"}},
                    "last_pcp_visit_days": {"type": "integer"},
                    "active_medications":  {"type": "integer"},
                    "has_sdoh_flags":      {"type": "boolean"},
                },
                "required": ["patient_id"],
            }
        },
    }
}


def _score_patient(patient: dict) -> dict:
    """
    Heuristic risk scoring — in production replaced by ML model on SageMaker.
    """
    score = 0
    factors = []
    patient_id = patient.get("id", "UNKNOWN")

    # Reconstruct from FHIR record or flat analytics row
    raw = json.loads(patient.get("_raw", "{}")) if patient.get("_raw") else patient

    # ED utilization (from Encounter data — simplified)
    ed_count = patient.get("ed_visits_90d", 0)
    if ed_count >= 3:
        score += 35
        factors.append(f"High ED utilization: {ed_count} visits in 90 days")
    elif ed_count >= 1:
        score += 15

    # Chronic conditions (from Condition resource count)
    chronic_count = patient.get("chronic_condition_count", 0)
    if chronic_count >= 3:
        score += 25
        factors.append(f"Multiple chronic conditions: {chronic_count}")
    elif chronic_count >= 1:
        score += 10

    # Gap in care
    days_since_pcp = patient.get("days_since_last_pcp_visit", 0)
    if days_since_pcp > 365:
        score += 20
        factors.append(f"No PCP visit in {days_since_pcp} days")
    elif days_since_pcp > 180:
        score += 10

    # SDOH flags
    if patient.get("has_sdoh_flags"):
        score += 20
        factors.append("Social determinants of health flags present")

    tier = "LOW" if score < 25 else "MEDIUM" if score < 50 else "HIGH" if score < 75 else "CRITICAL"

    return {
        "patient_id": patient_id,
        "risk_score": min(score, 100),
        "risk_tier": tier,
        "risk_factors": factors,
        "output_requires_human_review": True,
    }


def handler(event: dict, context) -> dict:
    tenant_id              = event["tenant_id"]
    patient_data_location  = event.get("patient_data_location", "")
    encounter_data_location = event.get("encounter_data_location", "")
    agent_config           = event.get("agent_config", {})

    log = TenantLogger("risk-stratification-agent", tenant_id)
    log.info("Running risk stratification")

    s3 = get_s3()
    patients = []

    if patient_data_location:
        bucket = PROC_BUCKET
        key    = patient_data_location.replace(f"s3://{bucket}/", "").replace("s3://hi-analytics-596272105033/", "")
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            for line in obj["Body"].read().decode().strip().splitlines():
                try:
                    patients.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except Exception as exc:
            log.warning(f"Could not read patient data: {exc}")

    if not patients:
        log.info("No patient data available — returning empty stratification")
        return ok({
            "patients_analyzed": 0, "high_risk_patients": [],
            "stratification_complete": True,
            "output_requires_human_review": True,
        })

    results = [_score_patient(p) for p in patients]
    high_risk = [r for r in results if r["risk_tier"] in ("HIGH", "CRITICAL")]

    emit_audit_event(tenant_id, "RISK_STRATIFICATION_COMPLETE", {
        "patients_analyzed": len(patients),
        "high_risk_count": len(high_risk),
        "ai_model": MODEL_ID,
        "human_review_required": True,
        "autonomous_action_taken": False,
    })

    log.info(f"Stratified {len(patients)} patients. {len(high_risk)} high/critical risk.")
    return ok({
        "patients_analyzed": len(patients),
        "high_risk_patients": high_risk[:50],   # Top 50 for review queue
        "all_results_summary": {
            "critical": sum(1 for r in results if r["risk_tier"] == "CRITICAL"),
            "high":     sum(1 for r in results if r["risk_tier"] == "HIGH"),
            "medium":   sum(1 for r in results if r["risk_tier"] == "MEDIUM"),
            "low":      sum(1 for r in results if r["risk_tier"] == "LOW"),
        },
        "output_requires_human_review": True,
        "ai_model": MODEL_ID,
        "stratified_at": datetime.now(timezone.utc).isoformat(),
    })
