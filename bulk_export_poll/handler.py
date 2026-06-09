"""
hi-bulk-export-poll
-------------------
Checks the async status of a FHIR $export job.
Returns status: "completed" | "in-progress" | "error"
"""

import os
import requests
import boto3
from datetime import datetime, timezone
from shared.utils import TenantLogger, ok

_ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-2"))


def _get_auth_header(tenant_id: str) -> dict:
    param = f"/healthinsight/tenants/{tenant_id}/fhir/access_token"
    token = _ssm.get_parameter(Name=param, WithDecryption=True)["Parameter"]["Value"]
    return {"Authorization": f"Bearer {token}"}


def handler(event: dict, context) -> dict:
    tenant_id   = event["tenant_id"]
    polling_url = event["polling_url"]
    job_id      = event["job_id"]

    log = TenantLogger("bulk-export-poll", tenant_id)
    log.info(f"Polling job_id={job_id}")

    headers = _get_auth_header(tenant_id)
    headers["Accept"] = "application/json"

    resp = requests.get(polling_url, headers=headers, timeout=30)

    if resp.status_code == 202:
        progress = resp.headers.get("X-Progress", "")
        log.info(f"Export in-progress: {progress}")
        return ok({"status": "in-progress", "job_id": job_id, "progress": progress})

    if resp.status_code == 200:
        data = resp.json()
        output_files = [
            {"type": f["type"], "url": f["url"]}
            for f in data.get("output", [])
        ]
        log.info(f"Export completed. {len(output_files)} files available.")
        return ok({
            "status": "completed",
            "job_id": job_id,
            "output_files": output_files,
            "transaction_time": data.get("transactionTime", ""),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

    log.error(f"Export error: HTTP {resp.status_code}")
    return ok({"status": "error", "job_id": job_id, "http_status": resp.status_code})
