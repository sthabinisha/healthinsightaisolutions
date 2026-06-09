# HealthInsight AI Solutions 
---

## Table of contents

1. [Project overview](#1-project-overview)
2. [Architecture summary](#2-architecture-summary)
3. [Repository structure](#3-repository-structure)
4. [AWS Step Functions workflow](#4-aws-step-functions-workflow)
5. [Lambda functions reference](#5-lambda-functions-reference)
6. [EHR connectors: Epic and eClinicalWorks](#6-ehr-connectors-epic-and-eclinicalworks)
7. [AI agent pipelines](#7-ai-agent-pipelines)
8. [PHI classification and HIPAA controls](#8-phi-classification-and-hipaa-controls)
9. [Data quality gate](#9-data-quality-gate)
10. [Vector database and RAG layer](#10-vector-database-and-rag-layer)
11. [Human review enforcement](#11-human-review-enforcement)
12. [Dashboard UI](#12-dashboard-ui)
13. [Environment variables](#13-environment-variables)
14. [SSM Parameter Store secrets](#14-ssm-parameter-store-secrets)
15. [IAM permissions](#15-iam-permissions)
16. [Local development setup](#16-local-development-setup)
17. [Deployment](#17-deployment)
18. [Infrastructure: AWS services used](#18-infrastructure-aws-services-used)
19. [Security and compliance design](#19-security-and-compliance-design)
20. [Audit trail and retention](#20-audit-trail-and-retention)
21. [Roadmap](#21-roadmap)
22. [Contact](#22-contact)

---

## 1. Project overview

HealthInsight AI Solutions is a healthcare technology platform that brings secure,
AI-enabled analytics and workflow automation to underserved healthcare organizations —
FQHCs, rural hospitals, independent practices, specialty clinics, and regional payers
that cannot adopt large enterprise AI platforms.

The MVP being built here is the core data pipeline: it ingests healthcare data from
multiple EHR sources (Epic FHIR R4, eClinicalWorks, HL7 v2, bulk export, flat files),
validates and normalizes it to FHIR R4 / USCDI v3 standards, runs three parallel AI
agent pipelines (claims denial risk, patient risk stratification, documentation gap
analysis), routes findings through mandatory human review, and writes a complete
HIPAA-aligned audit trail.

The entire pipeline is orchestrated by AWS Step Functions. Every Lambda function in
the state machine is implemented in this repository.

**What this is NOT:** An autonomous clinical decision system. Every AI output in this
platform is human-reviewable decision support. No claim is modified, no patient is
contacted, and no coding action is taken without explicit approval from an authorized
billing specialist.

---

## 2. Architecture summary

```
Client EHR / Data Source
        │
        ▼
┌─────────────────────────────────────────────────────────────────┐
│  AWS Step Functions — HealthInsight AI Workflow v1.0            │
│                                                                 │
│  ValidateAndAuthenticateTenant  ←── DynamoDB (tenant registry) │
│           │                         SSM (JWT secret, BAA)      │
│           ▼                                                     │
│  DetermineExtractionStrategy                                    │
│     ├── FHIR R4 API  (Epic / eCW OAuth2 / SMART RS384)         │
│     ├── HL7 v2 Feed  (SQS queue — ADT/ORM/ORU/DFT)            │
│     ├── Bulk Export  ($export → poll → download)               │
│     ├── Flat File    (S3 ingestion bucket → CSV/pipe/tab)      │
│     └── Multi-Source (parallel FHIR + HL7 → merge)            │
│           │                                                     │
│           ▼                                                     │
│  ClassifyPHIFields  ←── Comprehend Medical + 18 HIPAA fields   │
│           │                                                     │
│           ▼                                                     │
│  AssessDataQualityPerResourceType  (Map, MaxConcurrency=5)     │
│     └── ValidateSingleResourceType  per FHIR resource type     │
│           │                                                     │
│           ▼                                                     │
│  ComputeCompositeQualityScore                                   │
│           │                                                     │
│  QualityGateDecision ──────────────────────────────────────┐   │
│     ├── score ≥ 0.75  → NormalizeFHIRResources             │   │
│     ├── 0.60–0.75     → SendQualityWarningAndProceed       │   │
│     └── < 0.60        → NotifyQualityRemediationRequired ──┼─► WorkflowFailedQuality
│           │                                                 │   │
│           ▼                                                 │   │
│  NormalizeFHIRResources  (Map, MaxConcurrency=5)           │   │
│     └── NormalizeSingleResourceType  ←── ICD-10/CPT/LOINC │   │
│           │                                                     │
│           ▼                                                     │
│  StoreNormalizedDataToLake                                      │
│     └── S3 Parquet → Glue Catalog → Athena workgroup           │
│           │                                                     │
│           ▼                                                     │
│  RunParallelAnalysisPipelines  (Parallel)                       │
│     ├── ClaimsAnalysisPipeline   → Bedrock Strands / Claude    │
│     ├── RiskStratificationPipeline → Bedrock Strands / Claude  │
│     └── DocumentationGapPipeline  → rule-based + Bedrock       │
│           │                                                     │
│           ▼                                                     │
│  MergeAndPrioritizeResults                                      │
│           │                                                     │
│  ClassifyResultsByRiskLevel                                     │
│     ├── CRITICAL → EnqueueCriticalForReview                    │
│     │              └── MandatoryHumanReviewCritical (SQS+token, 4h SLA)
│     ├── HIGH     → EnqueueHighRiskForReview                    │
│     │              └── MandatoryHumanReviewHigh    (SQS+token, 24h SLA)
│     ├── MEDIUM   → StoreMediumRiskToDashboard                  │
│     └── LOW      → StoreLowRiskToDashboard                     │
│           │                                                     │
│           ▼                                                     │
│  ValidateReviewDecision                                         │
│     ├── APPROVED         → ProcessApprovedRecommendations      │
│     ├── PARTIALLY_APPROVED → ProcessApprovedRecommendations    │
│     └── REJECTED         → LogRejectedForFeedback              │
│           │                                                     │
│           ▼                                                     │
│  UpdateAuditTrail  ←── CloudWatch Logs (7yr) + S3 Glacier      │
│  UpdateDashboardMetrics  ←── DynamoDB + CloudWatch Metrics     │
│  SendCompletionNotification  ←── SNS                           │
│           │                                                     │
│           ▼                                                     │
│  WorkflowComplete                                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Repository structure

```
healthinsight-ai/
│
├── README.md                              ← This file
│
├── step-functions/
│   └── workflow.json                      ← Full Step Functions state machine definition
│
├── dashboard/
│   └── claims_dashboard.html             ← Claims dashboard UI (card view, color-coded status)
│
├── hi-lambdas/
│   ├── requirements.txt                  ← Python dependencies
│   ├── shared/
│   │   └── utils.py                      ← Shared: logging, AWS clients, audit helpers
│   └── src/
│       ├── validate_tenant/              ← JWT auth, BAA verification, tenant config
│       ├── fhir_extractor/               ← Epic + eCW FHIR R4 API extraction
│       ├── hl7_extractor/                ← HL7 v2 ADT/ORM/ORU/DFT parsing
│       ├── flatfile_extractor/           ← CSV / pipe-delimited / tab-delimited
│       ├── bulk_export_trigger/          ← FHIR $export kickoff
│       ├── bulk_export_poll/             ← Async export status polling
│       ├── bulk_export_download/         ← NDJSON download to S3 raw zone
│       ├── source_merger/               ← Multi-source deduplication (MRN+DOB+name)
│       ├── phi_classifier/              ← 18 HIPAA Safe Harbor identifiers + Comprehend Medical
│       ├── data_quality_agent/          ← FHIR R4 + USCDI v3 validation per resource type
│       ├── quality_aggregator/          ← Composite quality score (weighted average)
│       ├── quality_notifier/            ← SNS alerts for quality warnings / remediation
│       ├── fhir_normalizer/             ← ICD-10-CM / CPT / LOINC normalization
│       ├── data_lake_store/             ← S3 analytics zone + Glue catalog + Athena workgroup
│       ├── claims_analysis_agent/       ← Bedrock Strands agentic loop (4 tools)
│       ├── risk_stratification_agent/   ← Patient risk scoring (heuristic + Bedrock)
│       ├── documentation_agent/         ← Encounter documentation gap analysis
│       ├── results_merger/              ← Merge 3 pipeline outputs, compute revenue at risk
│       ├── result_enqueuer/             ← SQS enqueueing for human review queues
│       ├── review_escalator/            ← SLA timeout handler (CRITICAL → supervisor)
│       ├── apply_approved_actions/      ← Human-authorized action execution only
│       ├── rejection_logger/            ← Reviewer rejection logging + feedback loop
│       ├── dashboard_store/             ← DynamoDB dashboard layer (medium/low risk)
│       ├── audit_writer/               ← CloudWatch + S3 audit trail (7-year HIPAA)
│       ├── metrics_updater/            ← CloudWatch metrics + DynamoDB dashboard metrics
│       └── extraction_failure_notifier/ ← All-source failure SNS alert
│
└── docs/
    └── HealthInsight_AI_White_Paper_v8_Final.docx   ← Technical white paper
```

---

## 4. AWS Step Functions workflow

The state machine definition lives at `step-functions/workflow.json`.

**Key design decisions:**

| Decision | Implementation |
|---|---|
| Tenant isolation at auth | `ValidateAndAuthenticateTenant` verifies JWT, checks BAA active status, and resolves tenant config before any PHI is accessed |
| Fallback chain | FHIR R4 → HL7 v2 → Flat file. Each failure triggers a Pass state logging the reason before rerouting |
| Bulk export polling | `WaitForBulkExportPolling` (60s wait) → `PollBulkExportStatus` → Choice state. Re-polls on `in-progress`, falls back to HL7 on `error` |
| Quality gate | Scores below 0.60 fail the workflow entirely. 0.60–0.75 proceeds with warning flag. Only ≥0.75 proceeds clean |
| Parallel AI pipelines | `RunParallelAnalysisPipelines` runs all three agents concurrently (Step Functions Parallel state) |
| Human review gate | `sqs:sendMessage.waitForTaskToken` — workflow is paused until a billing specialist sends the task token back. 4h SLA for CRITICAL, 24h for HIGH |
| SLA enforcement | `HeartbeatSeconds` on both review states. Timeout triggers `HandleCriticalReviewTimeout` or `HandleReviewTimeout` → SNS escalation |
| Audit trail | Written at `UpdateAuditTrail` regardless of which path the workflow took (approved, rejected, escalated, or timed out) |

---

## 5. Lambda functions reference

All functions use `handler(event, context)` as the entry point.
All functions import from `shared/utils.py` via a Lambda layer.
All functions return `{"status": "ok", ...}` on success.
All custom exceptions are named to match Step Functions `Catch` error names in the state machine.

| Lambda name | Handler file | Purpose |
|---|---|---|
| `hi-validate-tenant` | `validate_tenant/handler.py` | JWT decode, BAA verification, tenant config resolution |
| `hi-fhir-extractor` | `fhir_extractor/handler.py` | Epic SMART RS384 + eCW OAuth2, paginates FHIR bundles |
| `hi-hl7-extractor` | `hl7_extractor/handler.py` | SQS HL7 v2 consumer, ADT/ORM/ORU/DFT → FHIR-compatible NDJSON |
| `hi-flatfile-extractor` | `flatfile_extractor/handler.py` | CSV/pipe/tab, column mapping config, type coercion |
| `hi-bulk-export-trigger` | `bulk_export_trigger/handler.py` | FHIR `$export` kickoff, returns polling URL |
| `hi-bulk-export-poll` | `bulk_export_poll/handler.py` | Polls async export status → `completed / in-progress / error` |
| `hi-bulk-export-download` | `bulk_export_download/handler.py` | Parallel NDJSON download from presigned URLs to S3 |
| `hi-source-merger` | `source_merger/handler.py` | Deterministic dedup on MRN + DOB + normalized family name |
| `hi-phi-classifier` | `phi_classifier/handler.py` | 18 HIPAA Safe Harbor identifiers, Comprehend Medical, PHI field tagging |
| `hi-data-quality-agent` | `data_quality_agent/handler.py` | FHIR R4 schema, USCDI v3, value set codes, duplicate detection |
| `hi-quality-aggregator` | `quality_aggregator/handler.py` | Record-count-weighted composite score across all resource types |
| `hi-quality-notifier` | `quality_notifier/handler.py` | SNS warning (proceed) or remediation-required (fail) notification |
| `hi-fhir-normalizer` | `fhir_normalizer/handler.py` | ICD-10-CM / CPT / LOINC system URIs, USCDI v3 elements, lineage metadata |
| `hi-data-lake-store` | `data_lake_store/handler.py` | S3 analytics zone, Glue Data Catalog registration, Athena workgroup provisioning |
| `hi-claims-analysis-agent` | `claims_analysis_agent/handler.py` | Bedrock Converse agentic loop, 4 tools, Claude 3.5 Sonnet |
| `hi-risk-stratification-agent` | `risk_stratification_agent/handler.py` | Patient risk scoring, ED utilization, chronic conditions, SDOH flags |
| `hi-documentation-agent` | `documentation_agent/handler.py` | Encounter completeness, medical necessity, prior auth flags |
| `hi-results-merger` | `results_merger/handler.py` | Merges 3 pipeline outputs, computes revenue at risk, assigns overall risk level |
| `hi-result-enqueuer` | `result_enqueuer/handler.py` | SQS send to CRITICAL or STANDARD review queue with SLA metadata |
| `hi-review-escalator` | `review_escalator/handler.py` | SLA timeout handler — SNS escalation, marks items PENDING_ESCALATION |
| `hi-apply-approved-actions` | `apply_approved_actions/handler.py` | Applies only human-authorized items; writes immutable approval record to S3 |
| `hi-rejection-logger` | `rejection_logger/handler.py` | Records rejections with reasons; stores feedback NDJSON for model improvement |
| `hi-dashboard-store` | `dashboard_store/handler.py` | DynamoDB write for medium/low risk items surfaced to dashboard UI |
| `hi-audit-writer` | `audit_writer/handler.py` | CloudWatch Logs (7-year retention) + S3 Glacier archive per HIPAA 164.312(b) |
| `hi-metrics-updater` | `metrics_updater/handler.py` | CloudWatch custom metrics per tenant + DynamoDB dashboard metrics row |
| `hi-extraction-failure-notifier` | `extraction_failure_notifier/handler.py` | All-source failure SNS alert with remediation steps |

---

## 6. EHR connectors: Epic and eClinicalWorks

### Epic FHIR R4 (SMART Backend Services)

Authentication uses SMART on FHIR Backend Services (RFC 7521 JWT client assertion, RS384):

```python
# Epic requires a private key registered in your Epic developer app
# The JWT is signed with RS384 and sent to the Epic token endpoint
assertion = pyjwt.encode({
    "iss": client_id,
    "sub": client_id,
    "aud": token_url,
    "jti": os.urandom(16).hex(),
    "exp": int(time.time()) + 300,
}, private_key, algorithm="RS384")
```

SSM path for the Epic private key PEM:
```
/healthinsight/tenants/{tenant_id}/fhir/private_key_pem
```

Resources extracted: `Patient, Encounter, Claim, Condition, Procedure, Coverage, Organization, Practitioner`

Prior authorization support uses the Da Vinci Prior Auth Support IG via CDS Hooks (planned Phase 2).
Bulk patient data pull uses the FHIR `$everything` operation.

### eClinicalWorks (eCW) OAuth2

Authentication uses standard OAuth2 client credentials flow:

```python
resp = requests.post(token_url, data={
    "grant_type": "client_credentials",
    "client_id": client_id,
    "client_secret": client_secret,      # from SSM
    "scope": "system/*.read",
})
```

SSM paths for eCW:
```
/healthinsight/tenants/{tenant_id}/fhir/client_secret
/healthinsight/tenants/{tenant_id}/fhir/api_key      (developer key header)
```

eCW is particularly important for this platform because it is one of the most widely
deployed EHR platforms among FQHCs and independent practices — precisely the
underserved market HealthInsight targets.

### Rate limit handling

Both extractors implement exponential backoff on HTTP 429:
- FHIR rate limit: 5 retries, 30s initial wait, 1.5x backoff
- General Lambda errors: 3 retries, 5s initial wait, 2x backoff

---

## 7. AI agent pipelines

All three agent pipelines use **Amazon Bedrock Converse API** with
**Claude 3.5 Sonnet** (`us.anthropic.claude-3-5-sonnet-20241022-v2:0`) via the
**Strands Agents** framework. Each pipeline runs as a separate branch in the
`RunParallelAnalysisPipelines` Step Functions Parallel state.

### Claims Analysis Agent (`hi-claims-analysis-agent`)

Agentic loop with 4 tools:

| Tool | What it does |
|---|---|
| `predict_claim_denial_risk` | Scores claim 0.0–1.0 for denial probability based on CPT codes, ICD-10, payer, and claim amount |
| `check_modifier_requirements` | Validates CPT modifiers against payer-specific rules (e.g. bilateral modifier -50, -RT/-LT) |
| `analyze_denial_patterns` | Vector similarity search over historical denials — returns matching precedents with CO/PR/OA denial codes |
| `generate_correction_recommendations` | Human-readable fix suggestions; explicitly flagged as requiring billing specialist review |

The agentic loop runs up to 6 iterations (`max_iterations = 6`). Each tool call
is logged with its result summary. The final text output and risk score are
returned to the Step Functions state machine.

### Risk Stratification Agent (`hi-risk-stratification-agent`)

Scores patients on:
- ED visit frequency (90-day window)
- Chronic condition count (from Condition resources)
- Days since last PCP visit (from Encounter resources)
- Active medication count
- Social determinants of health (SDOH) flags

Risk tiers: `LOW / MEDIUM / HIGH / CRITICAL`
Output: prioritized outreach list for care coordinators.
**No patient contact is initiated by the platform.** The list goes to human reviewers only.

In production: replace the heuristic scorer with a SageMaker endpoint call.

### Documentation Gap Agent (`hi-documentation-agent`)

Checks each Encounter for:
- Missing `reasonCode` (diagnosis linkage)
- Missing `serviceProvider`
- Missing `participant[ATND]` (attending provider)
- Incomplete `period` (start/end)
- E&M level coding documentation
- Prior authorization flags for inpatient/emergency encounters and high-cost CPT codes

Human review is required before any coding or documentation change.

---

## 8. PHI classification and HIPAA controls

`hi-phi-classifier` runs before any analytics processing. The workflow will not
proceed past this state if PHI classification fails.

**18 HIPAA Safe Harbor identifiers detected:**

1. Names — FHIR `name` field + regex
2. Geographic subdivisions — FHIR `address` field
3. Dates — full dates (except year) via regex `YYYY-MM-DD / MM/DD/YYYY`
4. Phone numbers — regex pattern
5. Fax numbers — treated as phone
6. Email addresses — regex pattern
7. Social security numbers — `\d{3}-\d{2}-\d{4}` pattern
8. Medical record numbers — `MRN:` prefix pattern
9. Health plan numbers — FHIR `Coverage.identifier`
10. Account numbers — `Acct#` prefix pattern
11. Certificate/license numbers — NPI 10-digit pattern
12. Vehicle identifiers — not common in FHIR; flagged if present
13. Device identifiers — FHIR `Device` resource fields
14. Web URLs — regex `https?://`
15. IP addresses — `\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3}` pattern
16. Biometric identifiers — flagged via Comprehend Medical
17. Full-face photographs — flagged on `Patient.photo` presence
18. Unique identifying numbers — general identifier fields

Amazon Comprehend Medical (`DetectPHI`) is called on free-text fields to catch
PHI in clinical notes and unstructured content.

Every PHI access event is emitted to CloudWatch Logs before extraction begins,
recording: `tenant_id, batch_id, purpose, minimum_necessary: true, authorized_by`.

---

## 9. Data quality gate

`hi-data-quality-agent` runs per FHIR resource type (Map state, MaxConcurrency=5).

**Per-resource quality dimensions:**

| Dimension | Weight | What it checks |
|---|---|---|
| Completeness | 30% | Ratio of non-null fields to total expected fields (recursive) |
| Required field presence | 35% | FHIR R4 required fields per resource type |
| USCDI v3 compliance | 15% | USCDI v3 required data elements present |
| Code validity | 10% | ICD-10-CM / CPT / LOINC structural format |
| Uniqueness | 10% | Duplicate resource ID detection |

**Composite score thresholds** (computed by `hi-quality-aggregator`, record-count weighted):

| Score | Outcome |
|---|---|
| ≥ 0.75 | Proceed to normalization — clean |
| 0.60 – 0.75 | Proceed with warning flag — SNS notification sent to client admin |
| < 0.60 | Workflow fails — `NotifyQualityRemediationRequired` sends field-level remediation report |

This gate prevents AI agents from running on data that is too poor to produce
reliable analytics. It is implemented as a hard Step Functions Choice state —
not an optional check.

---

## 10. Vector database and RAG layer

Two retrieval patterns power the AI agents:

### Clinical document RAG

Used by `DocumentationGapPipeline` and the administrative summarization module.

Pipeline:
1. **AWS Textract** — OCR and layout extraction (scanned PDFs, fax-originated documents)
2. **Amazon Comprehend Medical** — NER: diagnoses (ICD-10), medications (RxNorm), procedures (CPT), PHI spans
3. **Chunking** — with metadata: document type, tenant ID, source system, FHIR resource linkage, timestamp
4. **Amazon Bedrock Titan Embeddings v2** — dense vector generation
5. **Amazon OpenSearch Serverless (vector engine)** or **pgvector on Aurora PostgreSQL** — per-tenant isolated namespaces, HNSW indexing

Retrieved chunks are injected as grounding context into the Claude prompt.
AI output includes source chunk references so reviewers can verify the basis
for any summary.

### Claims and prior authorization similarity matching

Used by `ClaimsAnalysisPipeline` (`analyze_denial_patterns` tool).

The vector index over historical claims, denial reason codes, and prior auth
outcomes enables nearest-neighbor retrieval of precedent cases — surfacing
denial patterns that exact-match rule logic misses.

Similarity confidence thresholds are configurable per use case. Low-confidence
retrievals are flagged rather than used silently.

**PHI safety:** Raw document text is never returned directly to the application
layer. Only curated chunks with source attribution are passed to the AI model.
All vector operations are logged in the audit trail.

---

## 11. Human review enforcement

The most important safety design in this platform is the mandatory human review
gate for CRITICAL and HIGH risk findings.

Both `MandatoryHumanReviewCritical` and `MandatoryHumanReviewHigh` use the
Step Functions `sqs:sendMessage.waitForTaskToken` resource.

This means:
- The Step Functions execution is **completely paused** after sending the SQS message
- It will not continue until a billing specialist sends the task token back via the review UI
- `HeartbeatSeconds: 14400` (4h) for CRITICAL, `HeartbeatSeconds: 86400` (24h) for HIGH
- If the heartbeat expires: `HandleCriticalReviewTimeout` → SNS escalation to supervisor

The SQS message sent to the review queue includes:
- `task_token` — the Step Functions callback token
- `items_requiring_review` — the AI-generated findings
- `total_revenue_at_risk` — dollar amount at stake
- `review_instructions` — explicit reminder that AI outputs are decision-support only
- `ai_model_disclosure` — model ID and framework disclosed to the reviewer

`hi-apply-approved-actions` is the **only** Lambda that can act on AI output —
and only after the reviewer has explicitly approved each item. Every approved
action is written to S3 with `reviewer_id`, `reviewed_at`, and `override_reason`
in an immutable record.

Rejected items are logged by `hi-rejection-logger` and stored in
`model-feedback/{tenant_id}/{batch_id}/rejections.ndjson` for future
agent fine-tuning after compliance review.

---

## 12. Dashboard UI

`dashboard/claims_dashboard.html` is a standalone HTML file implementing the
claims submission dashboard — a card-view kanban board with color-coded status lanes.

**6 status lanes:**

| Lane | Color | Meaning |
|---|---|---|
| Processing | Purple | Pipeline is running |
| Pending review | Blue | Waiting for billing specialist review (SLA active) |
| Escalated | Pink | SLA exceeded — supervisor notified |
| Approved | Green | Reviewer approved — actions applied |
| Denied | Red | Payer denied or reviewer rejected |
| Queued | Amber | Waiting to enter pipeline |

**Each card shows:**
- Claim ID (monospace)
- Patient name
- Payer
- Time since submission
- Claim amount
- Risk badge (Critical / High / Medium / Low)
- SLA progress bar (turns red as deadline approaches)

**Filters:** All / Critical only / Pending review only

**Click any card** for full detail: provider, CPT codes, ICD-10 codes,
payer, reviewer status, revenue at risk.

**Metric summary bar:** Total submissions, revenue at risk, pending review count,
approved count, data quality score.

The dashboard reads from the DynamoDB `hi-dashboard-results` table, which is
written by `hi-dashboard-store` (medium/low risk items) and
`hi-metrics-updater` (per-batch metrics).

---

## 13. Environment variables

All Lambda functions read these environment variables. Set them via the Lambda
console, a deployment script, or `serverless.yml` / CDK stack.

| Variable | Description | Default |
|---|---|---|
| `AWS_REGION` | Deployment region | `us-east-2` |
| `AWS_ACCOUNT_ID` | AWS account ID | `596272105033` |
| `HI_ENV` | Environment (`dev / staging / prod`) | `dev` |
| `HI_RAW_BUCKET` | S3 raw zone — stores extracted NDJSON before processing | `hi-raw-{account}` |
| `HI_PROCESSED_BUCKET` | S3 processed zone — normalized FHIR + audit archives | `hi-processed-{account}` |
| `HI_ANALYTICS_BUCKET` | S3 analytics zone — flattened Parquet/JSON for Athena | `hi-analytics-{account}` |
| `HI_TENANT_TABLE` | DynamoDB tenant registry table | `hi-tenants` |
| `HI_AUDIT_TABLE` | DynamoDB audit events table | `hi-audit-events` |
| `HI_DASHBOARD_TABLE` | DynamoDB dashboard results table | `hi-dashboard-results` |
| `HI_AUDIT_LOG_GROUP` | CloudWatch log group for HIPAA audit trail | `/healthinsight/audit` |
| `HI_JWT_SECRET_PARAM` | SSM path for JWT signing secret | `/healthinsight/jwt-secret` |

---

## 14. SSM Parameter Store secrets

All secrets are stored in AWS Systems Manager Parameter Store (SecureString, KMS-encrypted).
No secrets are stored in environment variables or code.

```
/healthinsight/jwt-secret                                    ← JWT signing secret (all tenants)

/healthinsight/tenants/{tenant_id}/fhir/access_token         ← Bearer token (generic)
/healthinsight/tenants/{tenant_id}/fhir/client_secret        ← eCW OAuth2 client secret
/healthinsight/tenants/{tenant_id}/fhir/private_key_pem      ← Epic SMART RS384 private key (PEM)
/healthinsight/tenants/{tenant_id}/fhir/api_key              ← API-key auth header value
```

Lambda execution roles have `ssm:GetParameter` scoped to
`arn:aws:ssm:us-east-2:*:parameter/healthinsight/*` only.

---

## 15. IAM permissions

Each Lambda function should have a dedicated execution role. The minimum permissions
required across the platform are:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "S3TenantIsolated",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
      "Resource": "arn:aws:s3:::hi-*/*",
      "Condition": {
        "StringEquals": {"s3:prefix": "${aws:PrincipalTag/tenant_id}"}
      }
    },
    {
      "Sid": "DynamoDB",
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Query"],
      "Resource": "arn:aws:dynamodb:us-east-2:*:table/hi-*"
    },
    {
      "Sid": "SSMSecrets",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter"],
      "Resource": "arn:aws:ssm:us-east-2:*:parameter/healthinsight/*"
    },
    {
      "Sid": "BedrockInference",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:Converse"],
      "Resource": "arn:aws:bedrock:us-east-2::foundation-model/anthropic.*"
    },
    {
      "Sid": "ComprehendMedical",
      "Effect": "Allow",
      "Action": ["comprehendmedical:DetectPHI", "comprehendmedical:DetectEntitiesV2"],
      "Resource": "*"
    },
    {
      "Sid": "SNSSQSNotifications",
      "Effect": "Allow",
      "Action": ["sns:Publish", "sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage"],
      "Resource": "arn:aws:sqs:us-east-2:*:hi-*"
    },
    {
      "Sid": "CloudWatch",
      "Effect": "Allow",
      "Action": ["cloudwatch:PutMetricData", "logs:CreateLogGroup",
                 "logs:CreateLogStream", "logs:PutLogEvents", "logs:PutRetentionPolicy"],
      "Resource": "*"
    },
    {
      "Sid": "GlueAthena",
      "Effect": "Allow",
      "Action": ["glue:CreateDatabase", "glue:GetDatabase", "glue:CreateTable",
                 "glue:GetTable", "glue:UpdateTable", "athena:CreateWorkGroup", "athena:GetWorkGroup"],
      "Resource": "*"
    }
  ]
}
```

---

## 16. Local development setup

**Prerequisites:**
- Python 3.11+
- AWS CLI configured with appropriate credentials
- AWS SAM CLI (optional, for local Lambda testing)

**Install dependencies:**

```bash
cd hi-lambdas
pip install -r requirements.txt
```

**Set local environment variables:**

```bash
export AWS_REGION=us-east-2
export AWS_ACCOUNT_ID=596272105033
export HI_ENV=dev
export HI_RAW_BUCKET=hi-raw-dev
export HI_PROCESSED_BUCKET=hi-processed-dev
export HI_ANALYTICS_BUCKET=hi-analytics-dev
export HI_TENANT_TABLE=hi-tenants-dev
export HI_AUDIT_TABLE=hi-audit-events-dev
export HI_DASHBOARD_TABLE=hi-dashboard-results-dev
export HI_AUDIT_LOG_GROUP=/healthinsight/audit/dev
export HI_JWT_SECRET_PARAM=/healthinsight/dev/jwt-secret
```

**Run a single handler locally (example):**

```bash
cd hi-lambdas
python -c "
import sys; sys.path.insert(0, '.')
from src.data_quality_agent.handler import handler
result = handler({
    'tenant_id': 'test-tenant-001',
    'resource_type': 'Patient',
    's3_location': 's3://hi-raw-dev/raw/test-tenant-001/batch-001/',
    'validation_config': {'completeness_threshold': 0.75}
}, None)
print(result)
"
```

**Test the Step Functions workflow locally with SAM:**

```bash
sam local invoke ValidateAndAuthenticateTenant \
  --event events/validate_tenant_test.json \
  --env-vars env.json
```

---

## 17. Deployment

**Lambda deployment (per function):**

```bash
# Package with dependencies
cd hi-lambdas/src/claims_analysis_agent
pip install -r ../../requirements.txt -t ./package/
cp handler.py ./package/
cd package && zip -r ../deployment.zip . && cd ..

# Deploy
aws lambda update-function-code \
  --function-name hi-claims-analysis-agent \
  --zip-file fileb://deployment.zip \
  --region us-east-2
```

**Shared layer (recommended for production):**

```bash
# Build shared layer containing all dependencies + shared/utils.py
mkdir -p layer/python
pip install -r requirements.txt -t layer/python/
cp -r shared/ layer/python/
cd layer && zip -r ../shared-layer.zip python/

aws lambda publish-layer-version \
  --layer-name healthinsight-shared \
  --zip-file fileb://shared-layer.zip \
  --compatible-runtimes python3.11 \
  --region us-east-2
```

**Step Functions state machine:**

```bash
aws stepfunctions update-state-machine \
  --state-machine-arn arn:aws:states:us-east-2:596272105033:stateMachine:HealthInsightAIPipeline \
  --definition file://step-functions/workflow.json \
  --region us-east-2
```

---

## 18. Infrastructure: AWS services used

| Service | Purpose |
|---|---|
| **AWS Step Functions** | Pipeline orchestration — 40+ states, Choice/Map/Parallel/Wait/Task |
| **AWS Lambda** | 26 function implementations (Python 3.11) |
| **Amazon S3** | Raw zone, processed zone, analytics zone, audit archive (3 buckets minimum) |
| **Amazon DynamoDB** | Tenant registry, audit events, dashboard results |
| **AWS Systems Manager (SSM)** | EHR credentials, JWT secrets (SecureString) |
| **Amazon Bedrock** | Claude 3.5 Sonnet inference (Converse API), Titan Embeddings v2 |
| **Amazon Comprehend Medical** | PHI detection, clinical NER (ICD-10, RxNorm, CPT) |
| **Amazon SQS** | Human review queues (CRITICAL + STANDARD per tenant), HL7 v2 feed queue |
| **Amazon SNS** | Completion notifications, quality alerts, escalation alerts (per tenant topic) |
| **Amazon Glue** | Data Catalog registration of FHIR analytics tables |
| **Amazon Athena** | SQL analytics over normalized FHIR data (per-tenant workgroup) |
| **Amazon OpenSearch Serverless** | Vector database for clinical RAG and claims similarity |
| **AWS CloudWatch** | Custom metrics per tenant, audit log group (7-year retention), Lambda logs |
| **AWS CloudTrail** | API-level audit logging across all AWS services |
| **AWS Textract** | OCR and layout extraction for scanned healthcare documents |
| **AWS KMS** | Encryption key management for all S3 objects, SSM secrets |
| **AWS IAM** | Least-privilege execution roles, resource-based policies, tenant tag conditions |

---

## 19. Security and compliance design

**Encryption:**
- All S3 objects: `ServerSideEncryption: aws:kms` on every `put_object` call
- All DynamoDB tables: KMS-managed encryption at rest
- All data in transit: TLS 1.2 minimum (TLS 1.3 preferred via HTTPS endpoints)
- SSM SecureString: KMS-encrypted at rest

**Tenant isolation:**
- All S3 keys are prefixed with `{zone}/{tenant_id}/`
- IAM conditions enforce that Lambda execution roles can only access their tagged tenant's prefix
- DynamoDB items use `pk: TENANT#{tenant_id}` — no cross-tenant query is possible
- Vector database: per-tenant index namespaces with no cross-namespace retrieval permitted
- Athena: per-tenant workgroup, per-tenant Glue database (`healthinsight_{tenant_id}`)

**PHI access controls:**
- PHI classification runs before any analytics state
- Every PHI access event is logged to CloudWatch before data is read
- Minimum Necessary standard enforced: Lambda functions receive only the S3 prefix for their tenant
- No raw PHI is returned by the vector retrieval layer — only curated chunks with attribution

**BAA (Business Associate Agreement):**
- BAA status is verified at `ValidateAndAuthenticateTenant` before any PHI is accessed
- If BAA is not found or is expired: workflow fails with `BAANotFoundError` or `BAAExpiredError`
- AWS BAA must be executed before any PHI is processed on AWS infrastructure

**AI model disclosure:**
- Every audit event from an agent Lambda records: `model_id, framework, human_review_required: true, autonomous_action_taken: false`
- The SQS review message sent to billing specialists includes explicit `ai_model_disclosure`
- No AI output reaches clinical or billing staff without this disclosure

---

## 20. Audit trail and retention

`hi-audit-writer` is called at the end of every workflow execution path —
approved, rejected, escalated, timed out, or failed.

**What is recorded:**

| Field | Value |
|---|---|
| `workflow_id` | Step Functions execution ARN |
| `batch_id` | Client-supplied batch identifier |
| `tenant_id` | Tenant identifier |
| `phi_accessed` | `true` |
| `phi_classified` | `true` |
| `minimum_necessary` | `true` |
| `access_log_ref` | Reference to PHI classification output |
| `ai_usage.model_id` | `us.anthropic.claude-3-5-sonnet-20241022-v2:0` |
| `ai_usage.framework` | `strands-agents` |
| `ai_usage.human_review_enforced` | `true` |
| `ai_usage.autonomous_action_taken` | `false` |
| `pipeline_events` | Boolean flags for each major stage |

**Storage:**
- **CloudWatch Logs** — `/healthinsight/audit` — retention policy: 2,555 days (7 years), per HIPAA Security Rule 45 CFR §164.312(b)
- **S3 archive** — `processed/{tenant_id}/audit-archive/YYYY/MM/DD/{batch_id}.json` — S3 Lifecycle policy moves to Glacier after 90 days, retained for 7 years

---

## 21. Roadmap

| Phase | Timeframe | Key deliverables |
|---|---|---|
| **Phase 1 — Foundation** | Complete | Architecture, workflow JSON, Lambda implementations, white paper |
| **Phase 2 — MVP Build** | 0–3 months | Secure portal, role-based access, Epic + eCW connectors live, data quality dashboard, audit logging |
| **Phase 3 — Pilot** | 3–6 months | Pilot with 1–2 FQHCs or independent practices using non-production data. LOIs, user feedback, baseline metrics |
| **Phase 4 — Production Hardening** | 6–12 months | SageMaker risk model replacing heuristic, OpenSearch vector index live, monitoring runbooks, model cards, training materials |
| **Phase 5 — Scale** | 12+ months | Open Evidence integration (evidence-based clinical DB), Cadence chronic disease management module, additional payer connectors, packaged training offerings |




*All AI outputs in this platform are human-reviewable decision support.*
*No autonomous clinical or billing decisions are made by any component of this system.*
