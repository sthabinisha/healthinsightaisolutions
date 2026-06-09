import { Component } from '@angular/core';
import { AuthService } from '../../core/auth.service';

@Component({
  selector: 'hi-settings',
  standalone: true,
  template: `
    <div class="page-title">
      <div>
        <h1>Tenant settings</h1>
        <p>Show the tenant controls that gate whether a Step Functions pipeline can run.</p>
      </div>
    </div>

    <section class="grid grid-2">
      <article class="card">
        <div class="card-header">
          <h2>Tenant configuration</h2>
          <p>These values should be read from your HI_TenantConfig table.</p>
        </div>
        <div class="card-body settings-list">
          <div><strong>Tenant ID</strong><span>{{ auth.session()?.tenantId }}</span></div>
          <div><strong>BAA status</strong><span class="badge success">EXECUTED - demo</span></div>
          <div><strong>Allowed use cases</strong><span>claims_denial_analysis, documentation_gap_alerts, data_quality_dashboard</span></div>
          <div><strong>PHI processing</strong><span class="badge warning">Disabled for local demo</span></div>
        </div>
      </article>

      <article class="card">
        <div class="card-header">
          <h2>Roles</h2>
          <p>Start with these four roles and enforce them in API Gateway/Lambda too.</p>
        </div>
        <div class="card-body settings-list">
          <div><strong>administrator</strong><span>Tenant setup, users, policies, exports.</span></div>
          <div><strong>analyst</strong><span>Upload data, run approved analytics, prepare reports.</span></div>
          <div><strong>clinician-user</strong><span>Review outputs related to care operations.</span></div>
          <div><strong>auditor</strong><span>Read-only access to logs, reports, and evidence.</span></div>
        </div>
      </article>
    </section>
  `,
  styles: [`
    .settings-list { display: grid; gap: 14px; }
    .settings-list div { display: grid; gap: 6px; padding-bottom: 14px; border-bottom: 1px solid var(--border); }
    .settings-list div:last-child { padding-bottom: 0; border-bottom: 0; }
    .settings-list span:not(.badge) { color: var(--muted); line-height: 1.45; }
  `]
})
export class SettingsComponent {
  constructor(readonly auth: AuthService) {}
}
