# HealthInsight AI Angular Portal MVP

This is a buildable Angular starter portal for the first HealthInsight MVP.

It supports:

- local demo login
- tenant-aware dashboard
- secure-upload UI flow
- Step Functions pipeline start contract
- human-review queue
- review decisions: approve, needs revision, reject
- audit-log page
- tenant settings placeholder
- mock API mode so the UI runs before the backend exists

## Run locally

```bash
npm install
npm start
```

Open: `http://localhost:4200`

Demo login:

- email: `analyst@clinic.org`
- password: `demo-password`
- role: `analyst`

## How the portal maps to your AWS workflow

The upload page follows this sequence:

```text
User selects CSV
  ↓
POST /uploads/presign
  ↓
PUT file to S3 presigned URL
  ↓
POST /pipelines/start
  ↓
Step Functions starts HealthInsightDataPipelineStateMachine
  ↓
Review task appears in Human Review page
  ↓
Reviewer approves/rejects
  ↓
POST /review-tasks/:id/decision
  ↓
Backend calls SendTaskSuccess for the Step Functions task token
```

## Backend endpoints to implement

Set `mockApi: false` and update `apiBaseUrl` in `src/environments/environment.ts` when your API Gateway endpoints are ready.

```ts
export const environment = {
  production: false,
  mockApi: false,
  apiBaseUrl: 'https://YOUR_API_GATEWAY_ID.execute-api.us-east-1.amazonaws.com/dev'
};
```

Required endpoints:

```http
POST /uploads/presign
GET  /dashboard/metrics
GET  /batches
POST /pipelines/start
GET  /review-tasks
POST /review-tasks/{taskId}/decision
GET  /audit-events
```

## Suggested DynamoDB tables

- `HI_TenantConfig`
- `HI_BatchAudit`
- `HI_ReviewTasks`
- `HI_AuditEvents`

## Production replacements before PHI

This portal is not production-ready for PHI. Before real healthcare data:

- replace local demo login with Amazon Cognito or equivalent
- enforce JWT validation in API Gateway/Lambda
- enable tenant authorization on every backend endpoint
- use encrypted S3 buckets with tenant prefixes
- use CloudTrail and application-level audit logging
- store Step Functions task tokens securely
- execute AWS BAA and use HIPAA-eligible services only
- add access reviews, retention rules, backup, and incident-response procedures

## Main files

```text
src/app/features/upload/upload.component.ts     Upload and start workflow
src/app/features/review/review.component.ts     Human-review queue
src/app/features/dashboard/dashboard.component.ts Dashboard
src/app/features/audit/audit.component.ts       Audit evidence
src/app/core/api.service.ts                     Backend API contract
src/app/core/mock-data.ts                       Local demo data
```
