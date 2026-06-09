"""
hi-fhir-extractor
-----------------
Connects to an EHR FHIR R4 API (Epic, eClinicalWorks, or any ONC-certified
endpoint). Paginates through resource bundles and stores raw NDJSON to the
tenant-isolated S3 raw zone.

Supports:
  - Bearer token (Epic SMART on FHIR, eCW OAuth2)
  - API-key header (eCW developer key)
  - Exponential backoff on 429 rate-limit responses

Resource types extracted:
  Patient, Encounter, Claim, Condition, Procedure,
  Coverage, Organization, Practitioner
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Iterator

import boto3
import requests

from shared.utils import (
    TenantLogger, get_s3, get_client, emit_audit_event,
    ok, RAW_BUCKET, require_env,
)

# Secrets for EHR credentials stored in SSM Parameter Store
_ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-2"))
_token_cache: dict = {}


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class FHIRTimeoutError(Exception): pass
class FHIRRateLimitError(Exception): pass
class FHIRAPIUnavailableError(Exception): pass
class FHIRAuthError(Exception): pass


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_ecw_token(tenant_id: str, token_url: str, client_id: str, client_secret: str) -> str:
    """OAuth2 client credentials flow for eClinicalWorks."""
    cache_key = f"ecw:{tenant_id}"
    cached = _token_cache.get(cache_key)
    if cached and cached["expires_at"] > time.time() + 60:
        return cached["token"]

    resp = requests.post(token_url, data={
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "system/*.read",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    _token_cache[cache_key] = {
        "token": data["access_token"],
        "expires_at": time.time() + data.get("expires_in", 3600),
    }
    return data["access_token"]


def _get_epic_token(tenant_id: str, token_url: str, client_id: str, private_key_pem: str) -> str:
    """Epic SMART Backend Services (JWT client assertion)."""
    import jwt as pyjwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend

    cache_key = f"epic:{tenant_id}"
    cached = _token_cache.get(cache_key)
    if cached and cached["expires_at"] > time.time() + 60:
        return cached["token"]

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None, backend=default_backend()
    )
    assertion = pyjwt.encode({
        "iss": client_id,
        "sub": client_id,
        "aud": token_url,
        "jti": os.urandom(16).hex(),
        "exp": int(time.time()) + 300,
    }, private_key, algorithm="RS384")

    resp = requests.post(token_url, data={
        "grant_type": "client_credentials",
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": "system/Patient.read system/Encounter.read system/Claim.read "
                 "system/Condition.read system/Procedure.read system/Coverage.read",
    }, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    _token_cache[cache_key] = {
        "token": data["access_token"],
        "expires_at": time.time() + data.get("expires_in", 3600),
    }
    return data["access_token"]


def _get_auth_header(tenant_id: str, fhir_config: dict) -> dict:
    """Resolve auth header from tenant config."""
    auth_type = fhir_config.get("auth_type", "bearer")
    ssm_prefix = f"/healthinsight/tenants/{tenant_id}/fhir"

    if auth_type == "ecw_oauth2":
        secret = _ssm.get_parameter(Name=f"{ssm_prefix}/client_secret", WithDecryption=True)["Parameter"]["Value"]
        token = _get_ecw_token(
            tenant_id,
            fhir_config["token_url"],
            fhir_config["client_id"],
            secret,
        )
        return {"Authorization": f"Bearer {token}"}

    elif auth_type == "epic_smart":
        pem = _ssm.get_parameter(Name=f"{ssm_prefix}/private_key_pem", WithDecryption=True)["Parameter"]["Value"]
        token = _get_epic_token(
            tenant_id,
            fhir_config["token_url"],
            fhir_config["client_id"],
            pem,
        )
        return {"Authorization": f"Bearer {token}"}

    elif auth_type == "api_key":
        api_key = _ssm.get_parameter(Name=f"{ssm_prefix}/api_key", WithDecryption=True)["Parameter"]["Value"]
        return {fhir_config.get("api_key_header", "X-API-Key"): api_key}

    raise FHIRAuthError(f"Unknown auth_type: {auth_type}")


# ---------------------------------------------------------------------------
# FHIR pagination
# ---------------------------------------------------------------------------

def _fhir_get(url: str, headers: dict, params: dict = None, timeout: int = 60) -> dict:
    """Single FHIR GET with retry on 429."""
    for attempt in range(5):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=timeout)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 30))
                if attempt < 4:
                    time.sleep(retry_after)
                    raise FHIRRateLimitError(f"Rate limited, waited {retry_after}s")
            if resp.status_code in (502, 503, 504):
                if attempt < 4:
                    time.sleep(5 * (2 ** attempt))
                    continue
                raise FHIRAPIUnavailableError(f"FHIR API returned {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise FHIRTimeoutError("FHIR API timed out after retries")


def _paginate_resource(
    base_url: str, resource_type: str, headers: dict,
    since_date: str | None, page_size: int
) -> Iterator[dict]:
    """Yield all FHIR resources of a given type via Bundle pagination."""
    params = {"_count": page_size}
    if since_date:
        params["_lastUpdated"] = f"ge{since_date}"

    url = f"{base_url.rstrip('/')}/{resource_type}"
    page_count = 0

    while url:
        bundle = _fhir_get(url, headers, params if page_count == 0 else None)
        entries = bundle.get("entry", [])
        for entry in entries:
            yield entry.get("resource", {})

        # Follow next link
        url = None
        for link in bundle.get("link", []):
            if link.get("relation") == "next":
                url = link["url"]
                break
        page_count += 1


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    tenant_id       = event["tenant_id"]
    fhir_endpoint   = event["fhir_endpoint"]
    resource_types  = event.get("resource_types", [
        "Patient", "Encounter", "Claim", "Condition",
        "Procedure", "Coverage", "Organization", "Practitioner",
    ])
    since_date      = event.get("since_date")
    page_size       = event.get("page_size", 100)
    s3_raw_prefix   = event.get("s3_raw_prefix", f"raw/{tenant_id}/fhir/")

    log = TenantLogger("fhir-extractor", tenant_id)
    log.info(f"Extracting from {fhir_endpoint}, resources={resource_types}")

    # Resolve auth
    fhir_config = event.get("fhir_config", {})
    headers = _get_auth_header(tenant_id, fhir_config)
    headers["Accept"] = "application/fhir+json"

    s3 = get_s3()
    stats: dict[str, int] = {}
    s3_locations: list[str] = []

    for resource_type in resource_types:
        log.info(f"Extracting {resource_type}...")
        records = []
        try:
            for resource in _paginate_resource(fhir_endpoint, resource_type, headers, since_date, page_size):
                records.append(resource)
        except FHIRRateLimitError as exc:
            log.warning(f"Rate limit on {resource_type}: {exc}")

        stats[resource_type] = len(records)

        if records:
            key = f"{s3_raw_prefix}{resource_type}.ndjson"
            ndjson = "\n".join(json.dumps(r, default=str) for r in records)
            s3.put_object(
                Bucket=RAW_BUCKET,
                Key=key,
                Body=ndjson.encode(),
                ContentType="application/fhir+ndjson",
                ServerSideEncryption="aws:kms",
                Metadata={"tenant_id": tenant_id, "resource_type": resource_type},
            )
            s3_locations.append(key)
            log.info(f"Stored {len(records)} {resource_type} records → s3://{RAW_BUCKET}/{key}")

    total = sum(stats.values())
    emit_audit_event(tenant_id, "FHIR_EXTRACTION_COMPLETE", {
        "source": fhir_endpoint,
        "resource_stats": stats,
        "total_records": total,
        "s3_prefix": s3_raw_prefix,
    })

    log.info(f"Extraction complete. total_records={total}")
    return ok({
        "s3_location": f"s3://{RAW_BUCKET}/{s3_raw_prefix}",
        "s3_keys": s3_locations,
        "resource_types_extracted": resource_types,
        "record_counts": stats,
        "total_records": total,
        "extraction_method": "FHIR_R4_API",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    })
