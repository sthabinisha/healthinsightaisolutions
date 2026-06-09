"""
hi-hl7-extractor
----------------
Processes HL7 v2 messages from SQS queue or SFTP drop.
Parses ADT, ORM, ORU, DFT, BAR message types and maps to
FHIR R4-compatible structures stored in S3 raw zone.

Message types handled:
  ADT^A01 — Patient admission
  ADT^A08 — Patient information update
  ADT^A11 — Cancel admission
  ORM^O01 — Order message
  ORU^R01 — Observation result
  DFT^P03 — Detail financial transaction (billing)
  BAR^P01 — Add patient account (billing)
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

import boto3

from shared.utils import (
    TenantLogger, get_s3, get_sqs, emit_audit_event,
    ok, RAW_BUCKET,
)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class HL7ParseError(Exception): pass
class HL7ConnectionError(Exception): pass
class HL7SchemaError(Exception): pass


# ---------------------------------------------------------------------------
# HL7 v2 minimal parser
# ---------------------------------------------------------------------------

def _parse_hl7(raw: str) -> dict:
    """
    Parse a raw HL7 v2 message into a dict of segments.
    Returns {"MSH": {...}, "PID": {...}, "PV1": {...}, ...}
    """
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    if not lines or not lines[0].startswith("MSH"):
        raise HL7ParseError("Message does not start with MSH segment")

    # Detect field separator (char 3 of MSH)
    field_sep = lines[0][3]
    comp_sep  = lines[0][4] if len(lines[0]) > 4 else "^"

    segments: dict = {}
    for line in lines:
        seg_id = line[:3]
        fields = line.split(field_sep)
        segments.setdefault(seg_id, []).append(fields)

    return {"_field_sep": field_sep, "_comp_sep": comp_sep, "segments": segments}


def _extract_field(parsed: dict, segment: str, field_idx: int, comp_idx: int = 0) -> Optional[str]:
    """Safely extract a value from a parsed HL7 message."""
    segs = parsed["segments"].get(segment, [])
    if not segs:
        return None
    fields = segs[0]
    if field_idx >= len(fields):
        return None
    components = fields[field_idx].split(parsed["_comp_sep"])
    if comp_idx >= len(components):
        return None
    return components[comp_idx].strip() or None


# ---------------------------------------------------------------------------
# HL7 → FHIR-compatible mapping
# ---------------------------------------------------------------------------

def _adt_to_encounter(parsed: dict, tenant_id: str) -> dict:
    """Map ADT message to a FHIR Encounter-compatible dict."""
    patient_id = _extract_field(parsed, "PID", 3, 0) or "UNKNOWN"
    admit_date = _extract_field(parsed, "PV1", 44, 0)
    discharge_date = _extract_field(parsed, "PV1", 45, 0)
    facility = _extract_field(parsed, "PV1", 3, 3) or ""
    class_code = _extract_field(parsed, "PV1", 2, 0) or "unknown"

    return {
        "resourceType": "Encounter",
        "_source": "HL7_ADT",
        "tenant_id": tenant_id,
        "subject": {"reference": f"Patient/{patient_id}"},
        "class": {"code": class_code},
        "period": {
            "start": _hl7_date(admit_date),
            "end": _hl7_date(discharge_date),
        },
        "serviceProvider": {"display": facility},
        "_extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def _oru_to_observation(parsed: dict, tenant_id: str) -> dict:
    """Map ORU^R01 to FHIR Observation."""
    patient_id = _extract_field(parsed, "PID", 3, 0) or "UNKNOWN"
    obx_segments = parsed["segments"].get("OBX", [])
    observations = []
    for obx in obx_segments:
        observations.append({
            "resourceType": "Observation",
            "_source": "HL7_ORU",
            "tenant_id": tenant_id,
            "subject": {"reference": f"Patient/{patient_id}"},
            "code": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": obx[3].split("^")[0] if len(obx) > 3 else "",
                    "display": obx[3].split("^")[1] if len(obx) > 3 and "^" in obx[3] else "",
                }]
            },
            "valueString": obx[5] if len(obx) > 5 else "",
            "status": "final",
            "_extracted_at": datetime.now(timezone.utc).isoformat(),
        })
    return observations


def _dft_to_claim(parsed: dict, tenant_id: str) -> dict:
    """Map DFT^P03 detail financial transaction to FHIR Claim."""
    patient_id = _extract_field(parsed, "PID", 3, 0) or "UNKNOWN"
    ft1_segments = parsed["segments"].get("FT1", [])
    items = []
    for ft1 in ft1_segments:
        items.append({
            "sequence": len(items) + 1,
            "productOrService": {
                "coding": [{
                    "system": "http://www.ama-assn.org/go/cpt",
                    "code": ft1[7].split("^")[0] if len(ft1) > 7 else "",
                }]
            },
            "quantity": {"value": float(ft1[10]) if len(ft1) > 10 and ft1[10] else 1},
            "unitPrice": {"value": float(ft1[22]) if len(ft1) > 22 and ft1[22] else 0, "currency": "USD"},
        })

    return {
        "resourceType": "Claim",
        "_source": "HL7_DFT",
        "tenant_id": tenant_id,
        "patient": {"reference": f"Patient/{patient_id}"},
        "item": items,
        "status": "active",
        "_extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def _hl7_date(raw: Optional[str]) -> Optional[str]:
    """Convert HL7 date format (YYYYMMDDHHMMSS) to ISO 8601."""
    if not raw:
        return None
    raw = re.sub(r"[^0-9]", "", raw)
    try:
        if len(raw) >= 8:
            return datetime.strptime(raw[:14] if len(raw) >= 14 else raw[:8], "%Y%m%d%H%M%S" if len(raw) >= 14 else "%Y%m%d").isoformat()
    except ValueError:
        pass
    return raw


# ---------------------------------------------------------------------------
# SQS message reader
# ---------------------------------------------------------------------------

def _read_from_sqs(queue_url: str, max_messages: int = 100) -> list[str]:
    """Read up to max_messages HL7 messages from SQS."""
    sqs = get_sqs()
    messages = []
    receipt_handles = []

    while len(messages) < max_messages:
        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=min(10, max_messages - len(messages)),
            WaitTimeSeconds=5,
        )
        batch = resp.get("Messages", [])
        if not batch:
            break
        for m in batch:
            messages.append(m["Body"])
            receipt_handles.append((m["ReceiptHandle"],))

    # Delete processed messages
    for (rh,) in receipt_handles:
        try:
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=rh)
        except Exception:
            pass  # Non-fatal; message will be re-delivered after visibility timeout

    return messages


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    tenant_id    = event["tenant_id"]
    hl7_source   = event["hl7_source"]          # {"type": "sqs", "queue_url": "..."} or {"type": "sftp", ...}
    message_types = event.get("message_types", ["ADT^A01", "ADT^A08", "ORM^O01", "ORU^R01", "DFT^P03"])
    since_date   = event.get("since_date")
    s3_raw_prefix = event.get("s3_raw_prefix", f"raw/{tenant_id}/hl7/")

    log = TenantLogger("hl7-extractor", tenant_id)
    log.info(f"HL7 extraction from source type={hl7_source.get('type')}")

    # Fetch raw HL7 messages
    if hl7_source.get("type") == "sqs":
        raw_messages = _read_from_sqs(hl7_source["queue_url"])
    else:
        raise HL7ConnectionError(f"Unsupported HL7 source type: {hl7_source.get('type')}")

    log.info(f"Received {len(raw_messages)} HL7 messages")

    encounters, observations, claims, patients = [], [], [], []
    parse_errors = 0

    for raw in raw_messages:
        try:
            parsed = _parse_hl7(raw)
            segments = parsed["segments"]
            msh = segments.get("MSH", [[]])[0]
            msg_type = f"{msh[8].split('^')[0]}^{msh[8].split('^')[1]}" if len(msh) > 8 and "^" in msh[8] else ""

            if msg_type in ("ADT^A01", "ADT^A08", "ADT^A11"):
                encounters.append(_adt_to_encounter(parsed, tenant_id))
            elif msg_type == "ORU^R01":
                observations.extend(_oru_to_observation(parsed, tenant_id))
            elif msg_type in ("DFT^P03", "BAR^P01"):
                claims.append(_dft_to_claim(parsed, tenant_id))
        except HL7ParseError as exc:
            log.warning(f"Parse error (skipping message): {exc}")
            parse_errors += 1

    s3 = get_s3()
    stored_types = []

    def _store(records: list, resource_type: str):
        if not records:
            return
        key = f"{s3_raw_prefix}{resource_type}.ndjson"
        ndjson = "\n".join(json.dumps(r, default=str) for r in records)
        s3.put_object(
            Bucket=RAW_BUCKET, Key=key, Body=ndjson.encode(),
            ContentType="application/fhir+ndjson",
            ServerSideEncryption="aws:kms",
            Metadata={"tenant_id": tenant_id},
        )
        stored_types.append(resource_type)
        log.info(f"Stored {len(records)} {resource_type} records")

    _store(encounters, "Encounter")
    _store(observations, "Observation")
    _store(claims, "Claim")

    emit_audit_event(tenant_id, "HL7_EXTRACTION_COMPLETE", {
        "source_type": hl7_source.get("type"),
        "messages_received": len(raw_messages),
        "parse_errors": parse_errors,
        "encounters": len(encounters),
        "observations": len(observations),
        "claims": len(claims),
    })

    return ok({
        "s3_location": f"s3://{RAW_BUCKET}/{s3_raw_prefix}",
        "resource_types_extracted": stored_types,
        "extraction_method": "HL7_V2",
        "message_counts": {
            "total": len(raw_messages),
            "parse_errors": parse_errors,
            "encounters": len(encounters),
            "observations": len(observations),
            "claims": len(claims),
        },
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    })
