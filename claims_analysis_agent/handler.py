"""
hi-claims-analysis-agent
------------------------
Analyzes each claim for pre-submission denial risk using the
ClaimsAnalysisAgent powered by Amazon Bedrock Strands + Claude 3.5 Sonnet.

Tools available to the agent:
  predict_claim_denial_risk          — ML risk score from historical patterns
  check_modifier_requirements        — CPT modifier rule validation
  analyze_denial_patterns            — vector similarity over past denials
  generate_correction_recommendations — human-readable fix suggestions

ALL outputs require human review. No claim is modified or resubmitted
without explicit billing specialist approval.
"""

import json
import os
import boto3
from datetime import datetime, timezone

from shared.utils import TenantLogger, get_bedrock, emit_audit_event, ok, ACCOUNT_ID, REGION

MODEL_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"

# ---------------------------------------------------------------------------
# Tool definitions for Bedrock converse API (tool use)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "toolSpec": {
            "name": "predict_claim_denial_risk",
            "description": (
                "Predicts the probability that a claim will be denied based on "
                "procedure codes, diagnosis codes, payer, and historical denial patterns. "
                "Returns a risk score 0.0-1.0 and the top denial reasons."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "claim_id":       {"type": "string"},
                        "cpt_codes":      {"type": "array", "items": {"type": "string"}},
                        "icd10_codes":    {"type": "array", "items": {"type": "string"}},
                        "payer_id":       {"type": "string"},
                        "claim_amount":   {"type": "number"},
                    },
                    "required": ["claim_id", "cpt_codes"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "check_modifier_requirements",
            "description": (
                "Checks whether CPT procedure codes require modifiers based on "
                "payer-specific rules. Returns missing or incorrect modifiers."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "cpt_codes":  {"type": "array", "items": {"type": "string"}},
                        "payer_id":   {"type": "string"},
                        "place_of_service": {"type": "string"},
                    },
                    "required": ["cpt_codes"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "analyze_denial_patterns",
            "description": (
                "Searches the vector index of historical claim denials for cases "
                "similar to this claim. Returns top matching precedents and their "
                "denial reason codes (CO, PR, OA)."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "claim_text_summary": {"type": "string"},
                        "top_k":             {"type": "integer", "default": 5},
                    },
                    "required": ["claim_text_summary"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "generate_correction_recommendations",
            "description": (
                "Generates specific, actionable recommendations to reduce denial risk "
                "for this claim. Recommendations are informational only and require "
                "billing specialist review before any action."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "claim_id":          {"type": "string"},
                        "denial_risk_score": {"type": "number"},
                        "denial_reasons":    {"type": "array", "items": {"type": "string"}},
                        "missing_modifiers": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["claim_id", "denial_risk_score"],
                }
            },
        }
    },
]


# ---------------------------------------------------------------------------
# Tool executor (stub implementations — replace with real logic/DynamoDB queries)
# ---------------------------------------------------------------------------

def _execute_tool(tool_name: str, tool_input: dict, tenant_id: str) -> dict:
    if tool_name == "predict_claim_denial_risk":
        # In production: query DynamoDB denial-risk model or invoke SageMaker endpoint
        cpt = tool_input.get("cpt_codes", [])
        icd = tool_input.get("icd10_codes", [])
        # Simplified heuristic: flag high-cost procedures and certain code combos
        risk_score = 0.15
        reasons = []
        high_risk_cpts = {"99215", "99214", "27447", "43239", "70553"}
        if any(c in high_risk_cpts for c in cpt):
            risk_score += 0.30
            reasons.append("High-cost procedure code requires medical necessity documentation")
        if len(cpt) > 3:
            risk_score += 0.10
            reasons.append("Multiple procedure codes on single claim may require modifier 51")
        return {"risk_score": min(risk_score, 1.0), "denial_reasons": reasons, "model_version": "1.0"}

    elif tool_name == "check_modifier_requirements":
        cpt = tool_input.get("cpt_codes", [])
        missing = []
        # Simplified modifier rules
        bilateral_cpts = {"27447", "27446", "29881", "29882"}
        for c in cpt:
            if c in bilateral_cpts:
                missing.append(f"CPT {c} may require modifier -50 (bilateral) or -RT/-LT")
        return {"missing_modifiers": missing, "payer_specific_rules_applied": True}

    elif tool_name == "analyze_denial_patterns":
        # In production: call OpenSearch vector search
        summary = tool_input.get("claim_text_summary", "")
        return {
            "similar_denials": [
                {"denial_code": "CO-97", "description": "Payment adjusted because benefits not covered or not in force", "frequency": 0.23},
                {"denial_code": "CO-4",  "description": "Modifier required", "frequency": 0.18},
            ],
            "vector_search_confidence": 0.71,
        }

    elif tool_name == "generate_correction_recommendations":
        risk = tool_input.get("denial_risk_score", 0)
        reasons = tool_input.get("denial_reasons", [])
        recs = []
        if risk > 0.5:
            recs.append("Attach clinical notes documenting medical necessity before submission")
        if risk > 0.3:
            recs.append("Verify all procedure codes have appropriate diagnosis code linkage")
        for r in reasons:
            if "modifier" in r.lower():
                recs.append("Review modifier requirements with billing supervisor")
        return {"recommendations": recs, "requires_human_review": True, "ai_confidence": min(0.85, 1 - risk * 0.1)}

    return {"error": f"Unknown tool: {tool_name}"}


# ---------------------------------------------------------------------------
# Agentic loop using Bedrock Converse API
# ---------------------------------------------------------------------------

def _run_agent(claim: dict, tenant_id: str) -> dict:
    bedrock = get_bedrock()

    claim_summary = (
        f"Claim ID: {claim.get('id', 'UNKNOWN')}\n"
        f"Status: {claim.get('status', '')}\n"
        f"Patient: {claim.get('patient', {}).get('reference', '') if isinstance(claim.get('patient'), dict) else ''}\n"
        f"Items: {json.dumps(claim.get('item', [])[:5], default=str)}\n"
        f"Total: {json.dumps(claim.get('total', {}), default=str)}"
    )

    system_prompt = (
        "You are a healthcare claims analysis AI assistant for HealthInsight AI Solutions. "
        "Your role is to analyze medical claims for denial risk and provide decision-support "
        "recommendations to billing specialists. "
        "IMPORTANT: You are a decision-support tool only. All your outputs will be reviewed "
        "by an authorized billing specialist before any action is taken. "
        "You do NOT make autonomous decisions. You analyze and recommend only. "
        "Use the available tools to assess this claim thoroughly."
    )

    messages = [{"role": "user", "content": [{"text": f"Analyze this claim for denial risk:\n\n{claim_summary}"}]}]

    max_iterations = 6
    tool_calls_made = []

    for iteration in range(max_iterations):
        response = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": system_prompt}],
            messages=messages,
            toolConfig={"tools": TOOLS, "toolChoice": {"auto": {}}},
            inferenceConfig={"maxTokens": 1000, "temperature": 0.1},
        )

        stop_reason = response.get("stopReason", "")
        output_msg  = response["output"]["message"]
        messages.append(output_msg)

        if stop_reason == "end_turn":
            # Extract final text
            final_text = " ".join(
                block["text"] for block in output_msg.get("content", []) if "text" in block
            )
            return {
                "claim_id": claim.get("id", "UNKNOWN"),
                "analysis": final_text,
                "tool_calls": tool_calls_made,
                "iterations": iteration + 1,
            }

        if stop_reason == "tool_use":
            tool_results = []
            for block in output_msg.get("content", []):
                if block.get("type") == "tool_use" or "toolUse" in block:
                    tool_use = block.get("toolUse", block)
                    tool_name  = tool_use.get("name", "")
                    tool_input = tool_use.get("input", {})
                    tool_id    = tool_use.get("toolUseId", "")

                    result = _execute_tool(tool_name, tool_input, tenant_id)
                    tool_calls_made.append({"tool": tool_name, "result_summary": str(result)[:200]})

                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_id,
                            "content": [{"json": result}],
                        }
                    })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

    return {"claim_id": claim.get("id", "UNKNOWN"), "analysis": "Max iterations reached", "tool_calls": tool_calls_made}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    tenant_id   = event["tenant_id"]
    claim       = event["claim"]
    agent_config = event.get("agent_config", {})

    log = TenantLogger("claims-analysis-agent", tenant_id)
    log.info(f"Analyzing claim {claim.get('id', 'UNKNOWN')}")

    result = _run_agent(claim, tenant_id)

    # Parse risk from tool calls
    risk_score = 0.2  # default
    for tc in result.get("tool_calls", []):
        if "risk_score" in tc.get("result_summary", ""):
            try:
                import re
                match = re.search(r"risk_score.*?(\d\.\d+)", tc["result_summary"])
                if match:
                    risk_score = float(match.group(1))
            except Exception:
                pass

    risk_level = "LOW" if risk_score < 0.3 else "MEDIUM" if risk_score < 0.6 else "HIGH" if risk_score < 0.8 else "CRITICAL"

    emit_audit_event(tenant_id, "CLAIMS_AGENT_COMPLETED", {
        "claim_id": claim.get("id", "UNKNOWN"),
        "risk_score": risk_score,
        "risk_level": risk_level,
        "ai_model": MODEL_ID,
        "framework": "strands-agents",
        "human_review_required": True,
        "autonomous_action_taken": False,
    })

    return ok({
        "claim_id": claim.get("id", "UNKNOWN"),
        "risk_score": risk_score,
        "risk_level": risk_level,
        "analysis": result.get("analysis", ""),
        "tool_calls_made": len(result.get("tool_calls", [])),
        "output_requires_human_review": True,
        "ai_model": MODEL_ID,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
    })
