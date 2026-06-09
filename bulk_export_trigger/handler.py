"""
hi-bulk-export-trigger
----------------------
Initiates FHIR Bulk Data Access ($export) on the EHR server.
Returns polling URL for async job tracking by Step Functions Wait state.
"""

import os
import time
from datetime import datetime, timezone
import requests
import boto3

from shared.utils import TenantLogger, emit_audit_event, ok

_ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-2"))


def _get_auth_header(tenant_id: str) -> dict:
    param = f"/healthinsight/tenants/{tenant_id}/fhir/access_token"
    try:
        token = _ssm.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"]
        return {"Authorization": f"Bearer {token}"}
    except Exception as exc:
        raise RuntimeError(f"Could not retrieve FHIR token for {tenant_id}: {exc}")


def handler(event: dict, context) -> dict:
    tenant_id    = event["tenant_id"]
    fhir_endpoint = event["fhir_endpoint"].rstrip("/")
    export_types  = event.get("export_types", ["Patient", "Encounter", "Claim", "Condition", "Procedure"])
    since_date    = event.get("since", "")
    output_format = event.get("output_format", "application/fhir+ndjson")

    log = TenantLogger("bulk-export-trigger", tenant_id)
    log.info(f"Triggering $export on {fhir_endpoint}")

    headers = _get_auth_header(tenant_id)
    headers.update({"Accept": "application/fhir+json", "Prefer": "respond-async"})

    params = {
        "_type": ",".join(export_types),
        "_outputFormat": output_format,
    }
    if since_date:
        params["_since"] = since_date

    resp = requests.get(f"{fhir_endpoint}/$export", headers=headers, params=params, timeout=30)

    if resp.status_code == 202:
        polling_url = resp.headers.get("Content-Location", "")
        job_id = polling_url.rstrip("/").split("/")[-1]
        log.info(f"Bulk export accepted. job_id={job_id}")
        emit_audit_event(tenant_id, "BULK_EXPORT_TRIGGERED", {
            "job_id": job_id,
            "export_types": export_types,
            "fhir_endpoint": fhir_endpoint,
        })
        return ok({"polling_url": polling_url, "job_id": job_id, "triggered_at": datetime.now(timezone.utc).isoformat()})

    resp.raise_for_status()
    raise RuntimeError(f"Unexpected response {resp.status_code} from $export")
