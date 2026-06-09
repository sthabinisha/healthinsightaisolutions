import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';

@Component({
  selector: 'hi-upload',
  standalone: true,
  imports: [FormsModule, RouterLink],
  template: `
    <div class="page-title">
      <div>
        <h1>Upload data</h1>
        <p>Upload synthetic or approved operational data to start the AWS Step Functions pipeline.</p>
      </div>
      <a class="btn" routerLink="/audit">View audit log</a>
    </div>

    <div class="notice">
      For the MVP, use synthetic CSV data first. Do not upload real PHI until Cognito, BAA, tenant isolation, encryption, audit retention, and access review are fully configured.
    </div>

    <section class="grid grid-2">
      <article class="card">
        <div class="card-header">
          <h2>Start a pipeline execution</h2>
          <p>The portal requests a presigned S3 URL, uploads the file, then calls your backend to start the state machine.</p>
        </div>
        <div class="card-body">
          <form (ngSubmit)="submit()">
            <div class="form-row">
              <label for="useCase">Use case</label>
              <select id="useCase" name="useCase" [(ngModel)]="useCase">
                <option value="claims_denial_analysis">Claims denial analysis</option>
                <option value="documentation_gap_alerts">Documentation gap alerts</option>
                <option value="data_quality_dashboard">Data-quality dashboard</option>
                <option value="eligibility_precheck">Eligibility pre-check</option>
                <option value="administrative_summary">Administrative summary</option>
              </select>
            </div>

            <div class="form-row">
              <label for="file">CSV file</label>
              <input id="file" type="file" accept=".csv,.json,application/json,text/csv" (change)="onFileChange($event)">
              <small>Sample columns for claims MVP: claim_id, patient_id, payer, denial_reason, authorization_id, service_date, billed_amount.</small>
            </div>

            @if (selectedFile()) {
              <div class="selected-file">
                <strong>{{ selectedFile()?.name }}</strong>
                <span>{{ selectedFile()?.size ?? 0 }} bytes</span>
              </div>
            }

            @if (error()) {
              <p class="error">{{ error() }}</p>
            }

            @if (success()) {
              <p class="success-message">{{ success() }}</p>
            }

            <button class="btn primary" type="submit" [disabled]="isSubmitting()">
              {{ isSubmitting() ? 'Uploading and starting workflow...' : 'Upload and start workflow' }}
            </button>
          </form>
        </div>
      </article>

      <article class="card">
        <div class="card-header">
          <h2>Backend endpoint contract</h2>
          <p>Implement these API Gateway routes first.</p>
        </div>
        <div class="card-body endpoint-list">
          <div>
            <code>POST /uploads/presign</code>
            <span>Return S3 presigned URL, bucket, key, and batchId.</span>
          </div>
          <div>
            <code>POST /pipelines/start</code>
            <span>Call Step Functions StartExecution with tenantId, batchId, useCase, and S3 key.</span>
          </div>
          <div>
            <code>GET /batches</code>
            <span>Return batch execution status from DynamoDB.</span>
          </div>
          <div>
            <code>GET /review-tasks</code>
            <span>Return pending human-review tasks.</span>
          </div>
          <div>
            <code>POST /review-tasks/:id/decision</code>
            <span>Call SendTaskSuccess for approve/reject decisions.</span>
          </div>
        </div>
      </article>
    </section>
  `,
  styles: [`
    .selected-file { display: grid; gap: 4px; padding: 13px 14px; border-radius: 14px; border: 1px solid var(--border); background: var(--surface-2); margin-bottom: 16px; }
    .selected-file span { color: var(--muted); font-size: 13px; }
    .endpoint-list { display: grid; gap: 16px; }
    .endpoint-list div { display: grid; gap: 6px; padding-bottom: 14px; border-bottom: 1px solid var(--border); }
    .endpoint-list div:last-child { border-bottom: 0; padding-bottom: 0; }
    code { background: #0b2730; color: white; padding: 5px 7px; border-radius: 8px; width: fit-content; }
    .endpoint-list span { color: var(--muted); line-height: 1.45; }
    .error { color: var(--danger); font-weight: 800; }
    .success-message { color: var(--success); font-weight: 800; }
  `]
})
export class UploadComponent {
  useCase = 'claims_denial_analysis';
  selectedFile = signal<File | null>(null);
  isSubmitting = signal(false);
  error = signal('');
  success = signal('');

  constructor(
    private readonly api: ApiService,
    private readonly auth: AuthService,
    private readonly router: Router
  ) {}

  onFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.selectedFile.set(input.files?.item(0) ?? null);
    this.error.set('');
    this.success.set('');
  }

  submit(): void {
    const file = this.selectedFile();
    const session = this.auth.session();

    if (!file) {
      this.error.set('Select a CSV or JSON file first.');
      return;
    }
    if (!session) {
      this.error.set('Your session expired. Sign in again.');
      return;
    }

    this.isSubmitting.set(true);
    this.error.set('');
    this.success.set('');

    this.api.uploadAndStartPipeline(file, {
      tenantId: session.tenantId,
      useCase: this.useCase,
      uploadedBy: session.email
    }).subscribe({
      next: (batch) => {
        this.success.set(`Workflow started for ${batch.batchId}. Status: ${batch.status}.`);
        this.isSubmitting.set(false);
        setTimeout(() => void this.router.navigateByUrl('/review'), 900);
      },
      error: (err: unknown) => {
        this.error.set(err instanceof Error ? err.message : 'Upload failed.');
        this.isSubmitting.set(false);
      }
    });
  }
}
