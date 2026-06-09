import { Component, OnInit, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { ApiService } from '../../core/api.service';
import { AuditEvent } from '../../core/models';

@Component({
  selector: 'hi-audit',
  standalone: true,
  imports: [DatePipe],
  template: `
    <div class="page-title">
      <div>
        <h1>Audit log</h1>
        <p>Trace uploads, validation, AI usage, human-review decisions, publishing, and exports.</p>
      </div>
      <button class="btn" type="button" (click)="load()">Refresh</button>
    </div>

    <section class="card">
      <div class="card-header">
        <h2>Recent events</h2>
        <p>This should come from DynamoDB or your audit-log table in the backend.</p>
      </div>
      <div class="card-body table-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Event</th>
              <th>Batch</th>
              <th>Actor</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            @for (event of events(); track event.eventId) {
              <tr>
                <td>{{ event.createdAt | date:'medium' }}</td>
                <td><span class="badge info">{{ event.eventType }}</span></td>
                <td>{{ event.batchId }}</td>
                <td>{{ event.actor }}</td>
                <td>{{ event.detail }}</td>
              </tr>
            } @empty {
              <tr><td colspan="5"><div class="empty">No audit events yet.</div></td></tr>
            }
          </tbody>
        </table>
      </div>
    </section>
  `
})
export class AuditComponent implements OnInit {
  events = signal<AuditEvent[]>([]);

  constructor(private readonly api: ApiService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.api.getAuditEvents().subscribe(events => this.events.set(events));
  }
}
