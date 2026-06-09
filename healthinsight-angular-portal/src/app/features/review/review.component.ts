import { Component, OnInit, signal } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { ReviewTask } from '../../core/models';

@Component({
  selector: 'hi-review',
  standalone: true,
  imports: [DatePipe, DecimalPipe, FormsModule],
  template: `
    <div class="page-title">
      <div>
        <h1>Human review</h1>
        <p>Approve, reject, or send AI-assisted outputs back for revision before publication.</p>
      </div>
      <button class="btn" type="button" (click)="load()">Refresh</button>
    </div>

    <section class="grid">
      @for (task of tasks(); track task.taskId) {
        <article class="card review-card">
          <div class="card-header review-header">
            <div>
              <h2>{{ task.batchId }}</h2>
              <p>{{ formatUseCase(task.useCase) }} • Created {{ task.createdAt | date:'medium' }}</p>
            </div>
            <span class="badge" [class]="task.status === 'PENDING' ? 'badge warning' : 'badge success'">{{ task.status }}</span>
          </div>

          <div class="card-body">
            <div class="grid grid-3 task-meta">
              <div>
                <strong>Confidence</strong>
                <span>{{ task.confidence | percent:'1.0-0' }}</span>
              </div>
              <div>
                <strong>Review packet</strong>
                <span>{{ task.reviewPacketKey }}</span>
              </div>
              <div>
                <strong>Dashboard draft</strong>
                <span>{{ task.dashboardDraftKey }}</span>
              </div>
            </div>

            <div class="summary-box">
              <strong>AI-assisted summary</strong>
              <p>{{ task.summary }}</p>
              <small>Human review is required before any dashboard/report publication.</small>
            </div>

            @if (task.status === 'PENDING') {
              <div class="form-row">
                <label for="comments-{{ task.taskId }}">Reviewer comments</label>
                <textarea id="comments-{{ task.taskId }}" rows="3" [(ngModel)]="comments[task.taskId]" placeholder="Document why you approved, rejected, or requested revision."></textarea>
              </div>

              <div class="actions">
                <button class="btn success" type="button" (click)="decide(task, 'APPROVED')">Approve and publish</button>
                <button class="btn warning" type="button" (click)="decide(task, 'NEEDS_REVISION')">Needs revision</button>
                <button class="btn danger" type="button" (click)="decide(task, 'REJECTED')">Reject</button>
              </div>
            }
          </div>
        </article>
      } @empty {
        <div class="empty">No review tasks. Upload a batch to create one.</div>
      }
    </section>
  `,
  styles: [`
    .review-header { display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; }
    .review-card { overflow: hidden; }
    .task-meta { margin-bottom: 18px; }
    .task-meta div { padding: 14px; background: var(--surface-2); border-radius: 14px; min-width: 0; }
    .task-meta strong { display: block; margin-bottom: 6px; }
    .task-meta span { display: block; color: var(--muted); font-size: 13px; overflow-wrap: anywhere; }
    .summary-box { padding: 16px; border: 1px solid var(--border); border-radius: 14px; margin-bottom: 18px; }
    .summary-box p { line-height: 1.5; }
    .summary-box small { color: var(--muted); }
  `]
})
export class ReviewComponent implements OnInit {
  tasks = signal<ReviewTask[]>([]);
  comments: Record<string, string> = {};

  constructor(
    private readonly api: ApiService,
    private readonly auth: AuthService
  ) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.api.getReviewTasks().subscribe(tasks => this.tasks.set(tasks));
  }

  formatUseCase(value: string): string {
    return value.replaceAll('_', ' ');
  }

  decide(task: ReviewTask, decision: 'APPROVED' | 'NEEDS_REVISION' | 'REJECTED'): void {
    const session = this.auth.session();
    if (!session) return;

    this.api.submitReviewDecision({
      taskId: task.taskId,
      decision,
      reviewedBy: session.email,
      comments: this.comments[task.taskId] || `${decision} by ${session.email}`
    }).subscribe(() => this.load());
  }
}
