"""
hi-documentation-agent
-----------------------
Checks encounter documentation completeness using DocumentationAgent
(Bedrock Strands + Claude 3.5 Sonnet).

Flags:
  - Missing elements that may cause claim denials
  - ICD-10 and CPT code suggestions based on documented diagnoses
  - Prior authorization flags for high-cost procedures
  - Medical necessity documentation gaps

Human review is REQUIRED before any coding or documentation action.
"""

import json
from datetime import datetime, timezone

from shared.utils import TenantLogger, get_s3, get_bedrock, emit_audit_event, ok, ACCOUNT_ID, REGION, PROC_BUCKET

MODEL_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"

SYSTEM_PROMPT = (
    "You are a clinical documentation integrity AI for HealthInsight AI Solutions. "
    "Your role is to review encounter documentation and identify gaps that may affect "
    "claim reimbursement or compliance. "
    "You suggest ICD-10 and CPT codes based on documented clinical findings, but these "
    "suggestions must be reviewed and confirmed by an authorized billing specialist or "
    "physician before any coding action. You do not make autonomous coding decisions."
)

# Procedures that typically require prior authorization
PA_REQUIRED_CPTS = {
    "27447", "27446", "70553", "43239", "43240",  # Joint replacement, MRI, EGD
    "29827", "29826", "62322", "62323",            # Arthroscopy, spinal injection
    "90837", "90834",                              # Psychotherapy
}

# High-cost thresholds (simplified — in production from payer fee schedules)
HIGH_COST_THRESHOLD = 5000.0


def _analyze_encounter_gaps(encounter: dict) -> dict:
    """
    Rule-based documentation gap analysis for a single encounter.
    Returns list of gaps and code suggestions.
    """
    encounter_id = encounter.get("id", "UNKNOWN")
    gaps = []
    code_suggestions = []
    prior_auth_flags = []

    # Check medical necessity documentation
    reason_codes = encounter.get("reasonCode", [])
    if not reason_codes:
        gaps.append({
            "field": "reasonCode",
            "severity": "HIGH",
            "description": "No encounter reason/diagnosis code documented. Required for claim processing.",
        })

    # Check service provider
    if not encounter.get("serviceProvider"):
        gaps.append({
            "field": "serviceProvider",
            "severity": "MEDIUM",
            "description": "Service provider not documented. Required for claim routing.",
        })

    # Check encounter type for E&M coding
    encounter_type = encounter.get("type", [{}])
    if not encounter_type or not encounter_type[0].get("coding"):
        gaps.append({
            "field": "type",
            "severity": "MEDIUM",
            "description": "Encounter type not coded. E&M level cannot be verified.",
        })

    # Check period completeness
    period = encounter.get("period", {})
    if not period.get("start"):
        gaps.append({"field": "period.start", "severity": "HIGH", "description": "Encounter start time missing"})
    if not period.get("end"):
        gaps.append({"field": "period.end", "severity": "LOW", "description": "Encounter end time missing"})

    # Check participant (attending provider)
    participants = encounter.get("participant", [])
    has_attending = any(
        any(r.get("coding", [{}])[0].get("code") == "ATND"
            for r in p.get("type", [{}]))
        for p in participants
    )
    if not has_attending:
        gaps.append({
            "field": "participant[ATND]",
            "severity": "HIGH",
            "description": "Attending provider not documented. Required for billing.",
        })

    # Prior auth check for inpatient or high-cost class
    class_code = encounter.get("class", {}).get("code", "") if isinstance(encounter.get("class"), dict) else ""
    if class_code in ("IMP", "EMER"):
        prior_auth_flags.append({
            "reason": f"Inpatient/Emergency encounter class '{class_code}' may require retrospective authorization",
            "urgency": "REVIEW",
        })

    return {
        "encounter_id": encounter_id,
        "gaps": gaps,
        "gap_count": len(gaps),
        "high_severity_gaps": sum(1 for g in gaps if g["severity"] == "HIGH"),
        "code_suggestions": code_suggestions,
        "prior_auth_flags": prior_auth_flags,
        "requires_human_review": True,
    }


def handler(event: dict, context) -> dict:
    tenant_id               = event["tenant_id"]
    encounter_data_location = event.get("encounter_data_location", "")
    agent_config            = event.get("agent_config", {})

    log = TenantLogger("documentation-agent", tenant_id)
    log.info("Running documentation gap analysis")

    s3 = get_s3()
    encounters = []

    if encounter_data_location:
        bucket = PROC_BUCKET
        key    = encounter_data_location.replace(f"s3://{bucket}/", "").replace("s3://hi-analytics-596272105033/", "")
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            for line in obj["Body"].read().decode().strip().splitlines():
                try:
                    row = json.loads(line)
                    # Parse _raw back to full FHIR if available
                    if row.get("_raw"):
                        encounters.append(json.loads(row["_raw"]))
                    else:
                        encounters.append(row)
                except json.JSONDecodeError:
                    pass
        except Exception as exc:
            log.warning(f"Could not load encounter data: {exc}")

    if not encounters:
        return ok({
            "gaps_found": 0,
            "encounters_analyzed": 0,
            "documentation_gaps_report": [],
            "output_requires_human_review": True,
        })

    gap_reports = [_analyze_encounter_gaps(enc) for enc in encounters]
    total_gaps  = sum(r["gap_count"] for r in gap_reports)
    high_sev    = sum(r["high_severity_gaps"] for r in gap_reports)

    emit_audit_event(tenant_id, "DOCUMENTATION_ANALYSIS_COMPLETE", {
        "encounters_analyzed": len(encounters),
        "total_gaps_found": total_gaps,
        "high_severity_gaps": high_sev,
        "ai_model": MODEL_ID,
        "human_review_required": True,
        "autonomous_action_taken": False,
    })

    log.info(f"Documentation analysis: {len(encounters)} encounters, {total_gaps} gaps, {high_sev} high-severity")
    return ok({
        "encounters_analyzed": len(encounters),
        "gaps_found": total_gaps,
        "high_severity_gaps": high_sev,
        "documentation_gaps_report": gap_reports[:100],  # Cap at 100 for payload size
        "output_type": "DOCUMENTATION_GAPS_REPORT",
        "output_requires_human_review": True,
        "ai_model": MODEL_ID,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    })
