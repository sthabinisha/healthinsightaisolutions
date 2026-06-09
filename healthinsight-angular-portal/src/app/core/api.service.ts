import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, delay, map, of, switchMap } from 'rxjs';
import { environment } from '../../environments/environment';
import {
  AuditEvent,
  BatchSummary,
  DashboardMetrics,
  PresignedUploadRequest,
  PresignedUploadResponse,
  ReviewDecisionRequest,
  ReviewTask,
  StartPipelineRequest
} from './models';
import { MockApiStore } from './mock-data';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');

  constructor(private readonly http: HttpClient) {}

  getMetrics(): Observable<DashboardMetrics> {
    if (environment.mockApi) return of(MockApiStore.getMetrics()).pipe(delay(250));
    return this.http.get<DashboardMetrics>(`${this.baseUrl}/dashboard/metrics`);
  }

  getBatches(): Observable<BatchSummary[]> {
    if (environment.mockApi) return of(MockApiStore.getBatches()).pipe(delay(250));
    return this.http.get<BatchSummary[]>(`${this.baseUrl}/batches`);
  }

  getPresignedUpload(request: PresignedUploadRequest): Observable<PresignedUploadResponse> {
    if (environment.mockApi) return of(MockApiStore.createPresignedUpload(request.fileName)).pipe(delay(250));
    return this.http.post<PresignedUploadResponse>(`${this.baseUrl}/uploads/presign`, request);
  }

  uploadFile(uploadUrl: string, file: File): Observable<void> {
    if (environment.mockApi || uploadUrl.startsWith('mock://')) return of(void 0).pipe(delay(700));
    return this.http.put(uploadUrl, file, {
      headers: { 'Content-Type': file.type || 'text/csv' },
      responseType: 'text'
    }).pipe(map(() => void 0));
  }

  startPipeline(request: StartPipelineRequest): Observable<BatchSummary> {
    if (environment.mockApi) return of(MockApiStore.startPipeline(request)).pipe(delay(500));
    return this.http.post<BatchSummary>(`${this.baseUrl}/pipelines/start`, request);
  }

  uploadAndStartPipeline(file: File, request: Omit<PresignedUploadRequest, 'fileName' | 'contentType'> & { uploadedBy: string }): Observable<BatchSummary> {
    return this.getPresignedUpload({
      tenantId: request.tenantId,
      useCase: request.useCase,
      fileName: file.name,
      contentType: file.type || 'text/csv'
    }).pipe(
      switchMap((presigned) =>
        this.uploadFile(presigned.uploadUrl, file).pipe(
          switchMap(() => this.startPipeline({
            tenantId: request.tenantId,
            batchId: presigned.batchId,
            sourceType: 'UPLOAD',
            useCase: request.useCase,
            uploadedBy: request.uploadedBy,
            s3: {
              bucket: presigned.bucket,
              key: presigned.key
            }
          }))
        )
      )
    );
  }

  getReviewTasks(): Observable<ReviewTask[]> {
    if (environment.mockApi) return of(MockApiStore.getReviews()).pipe(delay(250));
    return this.http.get<ReviewTask[]>(`${this.baseUrl}/review-tasks`);
  }

  submitReviewDecision(request: ReviewDecisionRequest): Observable<ReviewTask> {
    if (environment.mockApi) {
      const task = MockApiStore.decideReview(request);
      if (!task) throw new Error('Review task not found.');
      return of(task).pipe(delay(400));
    }
    return this.http.post<ReviewTask>(`${this.baseUrl}/review-tasks/${request.taskId}/decision`, request);
  }

  getAuditEvents(): Observable<AuditEvent[]> {
    if (environment.mockApi) return of(MockApiStore.getAuditEvents()).pipe(delay(250));
    return this.http.get<AuditEvent[]>(`${this.baseUrl}/audit-events`);
  }
}
