import { AuditEvent, BatchSummary, DashboardMetrics, PresignedUploadResponse, ReviewDecisionRequest, ReviewTask, StartPipelineRequest } from './models';

const now = new Date();
const iso = (minutesAgo: number) => new Date(now.getTime() - minutesAgo * 60_000).toISOString();

let batches: BatchSummary[] = [
  {
    tenantId: 'demo-clinic-001',
    batchId: 'batch-demo-001',
    sourceType: 'UPLOAD',
    useCase: 'claims_denial_analysis',
    fileName: 'claims_sample.csv',
    uploadedBy: 'analyst@clinic.org',
    uploadedAt: iso(740),
    status: 'AWAITING_REVIEW',
    recordCount: 825,
    warningCount: 32,
    fatalErrorCount: 0,
    qualityScore: 91,
    reviewTaskId: 'review-demo-001'
  },
  {
    tenantId: 'demo-clinic-001',
    batchId: 'batch-demo-002',
    sourceType: 'UPLOAD',
    useCase: 'documentation_gap_alerts',
    fileName: 'encounter_docs.csv',
    uploadedBy: 'admin@clinic.org',
    uploadedAt: iso(1320),
    status: 'PUBLISHED',
    recordCount: 412,
    warningCount: 18,
    fatalErrorCount: 0,
    qualityScore: 94,
    reviewTaskId: 'review-demo-002'
  }
];

let reviews: ReviewTask[] = [
  {
    taskId: 'review-demo-001',
    tenantId: 'demo-clinic-001',
    batchId: 'batch-demo-001',
    useCase: 'claims_denial_analysis',
    status: 'PENDING',
    createdAt: iso(710),
    createdBy: 'analyst@clinic.org',
    reviewPacketKey: 'processed/demo-clinic-001/batch-demo-001/review-packet.json',
    dashboardDraftKey: 'processed/demo-clinic-001/batch-demo-001/dashboard-draft.json',
    summary: 'Potential denial drivers found: missing authorization number, inconsistent payer code, and incomplete documentation in 32 records. No autonomous billing decision has been made.',
    confidence: 0.86
  }
];

let auditEvents: AuditEvent[] = [
  {
    eventId: 'evt-001',
    tenantId: 'demo-clinic-001',
    batchId: 'batch-demo-001',
    eventType: 'UPLOAD_RECEIVED',
    actor: 'analyst@clinic.org',
    createdAt: iso(740),
    detail: 'Uploaded claims_sample.csv into raw tenant prefix.'
  },
  {
    eventId: 'evt-002',
    tenantId: 'demo-clinic-001',
    batchId: 'batch-demo-001',
    eventType: 'SCHEMA_VALIDATED',
    actor: 'system',
    createdAt: iso(735),
    detail: 'Schema validation completed with warnings only.'
  },
  {
    eventId: 'evt-003',
    tenantId: 'demo-clinic-001',
    batchId: 'batch-demo-001',
    eventType: 'HUMAN_REVIEW_CREATED',
    actor: 'system',
    createdAt: iso(710),
    detail: 'Review task created before publication.'
  }
];

export class MockApiStore {
  static getMetrics(): DashboardMetrics {
    const total = batches.length;
    const awaiting = batches.filter(b => b.status === 'AWAITING_REVIEW').length;
    const published = batches.filter(b => b.status === 'PUBLISHED' || b.status === 'COMPLETE').length;
    const scores = batches.map(b => b.qualityScore ?? 0).filter(score => score > 0);
    const average = scores.length ? Math.round(scores.reduce((sum, score) => sum + score, 0) / scores.length) : 0;
    return { totalBatches: total, awaitingReview: awaiting, published, averageQualityScore: average };
  }

  static getBatches(): BatchSummary[] {
    return [...batches].sort((a, b) => b.uploadedAt.localeCompare(a.uploadedAt));
  }

  static createPresignedUpload(fileName: string): PresignedUploadResponse {
    const batchId = `batch-${Date.now()}`;
    return {
      batchId,
      uploadUrl: 'mock://s3-presigned-upload-url',
      bucket: 'healthinsight-raw-dev',
      key: `raw/demo-clinic-001/${batchId}/${fileName}`
    };
  }

  static startPipeline(request: StartPipelineRequest): BatchSummary {
    const fileName = request.s3.key.split('/').pop() || 'upload.csv';
    const batch: BatchSummary = {
      tenantId: request.tenantId,
      batchId: request.batchId,
      sourceType: 'UPLOAD',
      useCase: request.useCase,
      fileName,
      uploadedBy: request.uploadedBy,
      uploadedAt: new Date().toISOString(),
      status: 'AWAITING_REVIEW',
      recordCount: 250,
      warningCount: 7,
      fatalErrorCount: 0,
      qualityScore: 92,
      reviewTaskId: `review-${request.batchId}`
    };
    batches = [batch, ...batches];

    const task: ReviewTask = {
      taskId: `review-${request.batchId}`,
      tenantId: request.tenantId,
      batchId: request.batchId,
      useCase: request.useCase,
      status: 'PENDING',
      createdAt: new Date().toISOString(),
      createdBy: request.uploadedBy,
      reviewPacketKey: `processed/${request.tenantId}/${request.batchId}/review-packet.json`,
      dashboardDraftKey: `processed/${request.tenantId}/${request.batchId}/dashboard-draft.json`,
      summary: 'The uploaded batch passed fatal validation and produced a draft administrative summary. Human approval is required before publication.',
      confidence: 0.82
    };
    reviews = [task, ...reviews];

    auditEvents = [
      {
        eventId: `evt-${Date.now()}-1`,
        tenantId: request.tenantId,
        batchId: request.batchId,
        eventType: 'STEP_FUNCTION_STARTED',
        actor: request.uploadedBy,
        createdAt: new Date().toISOString(),
        detail: 'Portal submitted StartPipeline request to backend.'
      },
      {
        eventId: `evt-${Date.now()}-2`,
        tenantId: request.tenantId,
        batchId: request.batchId,
        eventType: 'HUMAN_REVIEW_CREATED',
        actor: 'system',
        createdAt: new Date().toISOString(),
        detail: 'Mock review task created. Replace with SendTaskSuccess callback in production.'
      },
      ...auditEvents
    ];

    return batch;
  }

  static getReviews(): ReviewTask[] {
    return [...reviews].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  static decideReview(request: ReviewDecisionRequest): ReviewTask | undefined {
    const task = reviews.find(r => r.taskId === request.taskId);
    if (!task) return undefined;

    task.status = request.decision;
    const batch = batches.find(b => b.batchId === task.batchId);
    if (batch) {
      batch.status = request.decision === 'APPROVED' ? 'PUBLISHED' : request.decision;
    }
    auditEvents = [
      {
        eventId: `evt-${Date.now()}`,
        tenantId: task.tenantId,
        batchId: task.batchId,
        eventType: `REVIEW_${request.decision}`,
        actor: request.reviewedBy,
        createdAt: new Date().toISOString(),
        detail: request.comments || 'No reviewer comment provided.'
      },
      ...auditEvents
    ];
    return { ...task };
  }

  static getAuditEvents(): AuditEvent[] {
    return [...auditEvents].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
}
