import { Component, OnInit, signal } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { BatchSummary, DashboardMetrics } from '../../core/models';

@Component({
  selector: 'hi-dashboard',
  standalone: true,
  imports: [DatePipe, DecimalPipe, RouterLink],
  template: `
    <div class="page-title">
      <div>
        <h1>Dashboard</h1>
        <p>Track uploaded batches, validation status, human review, and published outputs.</p>
      </div>
      <a class="btn primary" routerLink="/upload">Upload new batch</a>
    </div>

    @if (metrics(); as m) {
      <section class="grid grid-4 metric-grid">
        <article class="card kpi">
          <div class="label">Total batches</div>
          <div class="value">{{ m.totalBatches }}</div>
          <div class="hint">All tenant batches</div>
        </article>
        <article class="card kpi">
          <div class="label">Awaiting human review</div>
          <div class="value">{{ m.awaitingReview }}</div>
          <div class="hint">Must be approved before publication</div>
        </article>
        <article class="card kpi">
          <div class="label">Published</div>
          <div class="value">{{ m.published }}</div>
          <div class="hint">Reviewed and released</div>
        </article>
        <article class="card kpi">
          <div class="label">Average quality score</div>
          <div class="value">{{ m.averageQualityScore }}%</div>
          <div class="hint">Based on completed validations</div>
        </article>
      </section>
    }

    <section class="card recent">
      <div class="card-header">
        <h2>Recent workflow batches</h2>
        <p>These map to Step Functions executions started after secure upload.</p>
      </div>
      <div class="card-body table-wrap">
        <table>
          <thead>
            <tr>
              <th>Batch</th>
              <th>Use case</th>
              <th>Status</th>
              <th>Quality</th>
              <th>Records</th>
              <th>Uploaded</th>
            </tr>
          </thead>
          <tbody>
            @for (batch of batches(); track batch.batchId) {
              <tr>
                <td>
                  <strong>{{ batch.batchId }}</strong><br>
                  <span class="muted">{{ batch.fileName }}</span>
                </td>
                <td>{{ formatUseCase(batch.useCase) }}</td>
                <td><span class="badge" [class]="statusClass(batch.status)">{{ batch.status }}</span></td>
                <td>{{ batch.qualityScore ?? 0 | number:'1.0-0' }}%</td>
                <td>{{ batch.recordCount | number }}</td>
                <td>{{ batch.uploadedAt | date:'medium' }}</td>
              </tr>
            } @empty {
              <tr><td colspan="6"><div class="empty">No batches yet. Upload a sample CSV to start.</div></td></tr>
            }
          </tbody>
        </table>
      </div>
    </section>
  `,
  styles: [`
    .grid-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .metric-grid { margin-bottom: 20px; }
    .recent { margin-top: 20px; }
    .muted { color: var(--muted); font-size: 13px; }
    @media (max-width: 1100px) { .grid-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 700px) { .grid-4 { grid-template-columns: 1fr; } }
  `]
})
export class DashboardComponent implements OnInit {
  metrics = signal<DashboardMetrics | null>(null);
  batches = signal<BatchSummary[]>([]);

  constructor(private readonly api: ApiService) {}

  ngOnInit(): void {
    this.api.getMetrics().subscribe(m => this.metrics.set(m));
    this.api.getBatches().subscribe(b => this.batches.set(b));
  }

  formatUseCase(value: string): string {
    return value.replaceAll('_', ' ');
  }

  statusClass(status: string): string {
    if (status === 'PUBLISHED' || status === 'COMPLETE') return 'badge success';
    if (status === 'AWAITING_REVIEW' || status === 'NEEDS_REVISION') return 'badge warning';
    if (status === 'REJECTED' || status === 'VALIDATION_FAILED') return 'badge danger';
    return 'badge info';
  }
}
