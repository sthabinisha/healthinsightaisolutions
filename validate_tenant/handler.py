"""
hi-validate-tenant
------------------
Validates tenant JWT, resolves tenant config, verifies BAA is active
before any PHI is accessed.

Raises:
  TenantAuthError   — JWT invalid or tenant not found
  BAANotFoundError  — no Business Associate Agreement on file
  BAAExpiredError   — BAA exists but past expiry date
"""

import json
import os
import time
from datetime import datetime, timezone

import boto3
import jwt  # PyJWT

from shared.utils import (
    TenantLogger, dynamo_get_tenant, emit_audit_event,
    ok, TENANT_TABLE, require_env,
)

JWT_SECRET_PARAM = os.environ.get("HI_JWT_SECRET_PARAM", "/healthinsight/jwt-secret")
_ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-2"))
_jwt_secret: str | None = None


def _get_jwt_secret() -> str:
    global _jwt_secret
    if _jwt_secret is None:
        resp = _ssm.get_parameter(Name=JWT_SECRET_PARAM, WithDecryption=True)
        _jwt_secret = resp["Parameter"]["Value"]
    return _jwt_secret


def _decode_jwt(token: str) -> dict:
    try:
        return jwt.decode(token, _get_jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise TenantAuthError("JWT has expired")
    except jwt.InvalidTokenError as exc:
        raise TenantAuthError(f"Invalid JWT: {exc}")


def _verify_baa(tenant_record: dict) -> None:
    baa = tenant_record.get("baa", {})
    if not baa.get("S") and not baa.get("M"):
        raise BAANotFoundError("No Business Associate Agreement found for tenant")

    # BAA stored as map with expiry_date field
    baa_map = tenant_record.get("baa_details", {}).get("M", {})
    expiry_str = baa_map.get("expiry_date", {}).get("S")
    if expiry_str:
        expiry = datetime.fromisoformat(expiry_str)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expiry:
            raise BAAExpiredError(f"BAA expired on {expiry_str}. Renewal required before PHI access.")


def _build_tenant_config(tenant_record: dict) -> dict:
    """Flatten DynamoDB item into a clean config dict."""
    def deser(v):
        if "S" in v: return v["S"]
        if "N" in v: return float(v["N"])
        if "BOOL" in v: return v["BOOL"]
        if "M" in v: return {k: deser(val) for k, val in v["M"].items()}
        if "L" in v: return [deser(i) for i in v["L"]]
        return None

    return {k: deser(v) for k, v in tenant_record.items()}


# ---------------------------------------------------------------------------
# Custom exceptions (Step Functions Catch uses these class names)
# ---------------------------------------------------------------------------

class TenantAuthError(Exception): pass
class BAANotFoundError(Exception): pass
class BAAExpiredError(Exception): pass


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    tenant_id    = event["tenant_id"]
    batch_id     = event["batch_id"]
    req_context  = event.get("request_context", {})

    log = TenantLogger("validate-tenant", tenant_id)
    log.info(f"Starting validation for batch={batch_id}")

    # 1. Verify JWT from request context
    token = req_context.get("jwt_token")
    if token:
        claims = _decode_jwt(token)
        if claims.get("tenant_id") != tenant_id:
            raise TenantAuthError("JWT tenant_id mismatch")
        log.info("JWT verified")

    # 2. Fetch tenant record from DynamoDB
    tenant_record = dynamo_get_tenant(tenant_id)
    if not tenant_record:
        raise TenantAuthError(f"Tenant '{tenant_id}' not found in registry")

    # 3. Check tenant is active
    status = tenant_record.get("status", {}).get("S", "")
    if status != "ACTIVE":
        raise TenantAuthError(f"Tenant status is '{status}', expected ACTIVE")

    # 4. Verify BAA
    _verify_baa(tenant_record)
    log.info("BAA verified and active")

    # 5. Build config object for downstream states
    config = _build_tenant_config(tenant_record)

    # 6. Emit audit event — PHI access gate opened
    emit_audit_event(tenant_id, "TENANT_AUTHENTICATED", {
        "batch_id": batch_id,
        "baa_verified": True,
        "request_ip": req_context.get("source_ip", "unknown"),
        "initiator": req_context.get("user_id", "system"),
    })

    log.info("Tenant validated — workflow authorised to proceed")
    return ok({
        "tenant_id": tenant_id,
        "tenant_name": config.get("tenant_name", ""),
        "baa_active": True,
        "feature_flags": config.get("feature_flags", {}),
        "notification_config": config.get("notification_config", {}),
        "data_retention_days": config.get("data_retention_days", 2555),  # 7 years
        "validated_at": datetime.now(timezone.utc).isoformat(),
    })
