"""
hi-bulk-export-download
-----------------------
Downloads NDJSON bulk export files from EHR-provided presigned URLs
to the HealthInsight tenant raw zone in S3.
"""

import os
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from shared.utils import TenantLogger, get_s3, emit_audit_event, ok, RAW_BUCKET


def _download_one(tenant_id: str, file_meta: dict, s3_prefix: str, s3) -> dict:
    """Download a single NDJSON file and upload to S3."""
    resource_type = file_meta["type"]
    url = file_meta["url"]

    resp = requests.get(url, stream=True, timeout=300)
    resp.raise_for_status()
    content = resp.content

    key = f"{s3_prefix}{resource_type}.ndjson"
    s3.put_object(
        Bucket=RAW_BUCKET,
        Key=key,
        Body=content,
        ContentType="application/fhir+ndjson",
        ServerSideEncryption="aws:kms",
        Metadata={"tenant_id": tenant_id, "resource_type": resource_type},
    )
    record_count = content.count(b"\n") + 1 if content else 0
    return {"resource_type": resource_type, "s3_key": key, "record_count": record_count}


def handler(event: dict, context) -> dict:
    tenant_id     = event["tenant_id"]
    export_manifest = event["export_manifest"]   # list of {"type": ..., "url": ...}
    s3_raw_prefix = event.get("s3_raw_prefix", f"raw/{tenant_id}/bulk/")

    log = TenantLogger("bulk-export-download", tenant_id)
    log.info(f"Downloading {len(export_manifest)} export files")

    s3 = get_s3()
    results = []

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_download_one, tenant_id, fm, s3_raw_prefix, s3): fm
            for fm in export_manifest
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                log.error(f"Download failed for {futures[future]}: {exc}")

    total_records = sum(r["record_count"] for r in results)
    resource_types = [r["resource_type"] for r in results]

    emit_audit_event(tenant_id, "BULK_EXPORT_DOWNLOADED", {
        "files_downloaded": len(results),
        "total_records": total_records,
        "resource_types": resource_types,
        "s3_prefix": s3_raw_prefix,
    })

    log.info(f"Download complete. {total_records} records across {len(results)} resource types")
    return ok({
        "s3_location": f"s3://{RAW_BUCKET}/{s3_raw_prefix}",
        "resource_types_extracted": resource_types,
        "record_counts": {r["resource_type"]: r["record_count"] for r in results},
        "total_records": total_records,
        "extraction_method": "BULK_EXPORT",
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    })
