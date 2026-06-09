export type UserRole = 'administrator' | 'analyst' | 'clinician-user' | 'auditor';

export interface UserSession {
  token: string;
  name: string;
  email: string;
  tenantId: string;
  role: UserRole;
}

export type BatchStatus =
  | 'UPLOADED'
  | 'VALIDATING'
  | 'VALIDATION_FAILED'
  | 'NORMALIZING'
  | 'ANALYZING'
  | 'AWAITING_REVIEW'
  | 'NEEDS_REVISION'
  | 'REJECTED'
  | 'PUBLISHED'
  | 'COMPLETE';

export interface BatchSummary {
  tenantId: string;
  batchId: string;
  sourceType: 'UPLOAD' | 'FHIR_API' | 'HL7_V2' | 'BULK_EXPORT';
  useCase: string;
  fileName: string;
  uploadedBy: string;
  uploadedAt: string;
  status: BatchStatus;
  recordCount: number;
  warningCount: number;
  fatalErrorCount: number;
  qualityScore?: number;
  reviewTaskId?: string;
}

export interface PresignedUploadRequest {
  tenantId: string;
  useCase: string;
  fileName: string;
  contentType: string;
}

export interface PresignedUploadResponse {
  uploadUrl: string;
  bucket: string;
  key: string;
  batchId: string;
}

export interface StartPipelineRequest {
  tenantId: string;
  batchId: string;
  sourceType: 'UPLOAD';
  useCase: string;
  uploadedBy: string;
  s3: {
    bucket: string;
    key: string;
  };
}

export interface ReviewTask {
  taskId: string;
  tenantId: string;
  batchId: string;
  useCase: string;
  status: 'PENDING' | 'APPROVED' | 'NEEDS_REVISION' | 'REJECTED';
  createdAt: string;
  createdBy: string;
  reviewPacketKey: string;
  dashboardDraftKey: string;
  summary: string;
  confidence: number;
}

export interface ReviewDecisionRequest {
  taskId: string;
  decision: 'APPROVED' | 'NEEDS_REVISION' | 'REJECTED';
  reviewedBy: string;
  comments: string;
}

export interface AuditEvent {
  eventId: string;
  tenantId: string;
  batchId: string;
  eventType: string;
  actor: string;
  createdAt: string;
  detail: string;
}

export interface DashboardMetrics {
  totalBatches: number;
  awaitingReview: number;
  published: number;
  averageQualityScore: number;
}
