import { Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../core/auth.service';

@Component({
  selector: 'hi-shell',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <div class="shell">
      <aside class="sidebar">
        <div class="brand">
          <div class="logo">HI</div>
          <div>
            <strong>HealthInsight AI</strong>
            <span>Secure portal MVP</span>
          </div>
        </div>

        <nav>
          <a routerLink="/dashboard" routerLinkActive="active">Dashboard</a>
          <a routerLink="/upload" routerLinkActive="active">Upload Data</a>
          <a routerLink="/review" routerLinkActive="active">Human Review</a>
          <a routerLink="/audit" routerLinkActive="active">Audit Log</a>
          <a routerLink="/settings" routerLinkActive="active">Tenant Settings</a>
        </nav>

        <div class="sidebar-footer">
          <span class="badge info">{{ auth.session()?.role }}</span>
          <button class="btn" type="button" (click)="auth.logout()">Sign out</button>
        </div>
      </aside>

      <main>
        <header class="topbar">
          <div>
            <strong>{{ auth.session()?.tenantId }}</strong>
            <span>{{ auth.session()?.email }}</span>
          </div>
          <span class="badge success">PHI-safe demo mode</span>
        </header>

        <section class="content">
          <router-outlet />
        </section>
      </main>
    </div>
  `,
  styles: [`
    .shell { min-height: 100vh; display: grid; grid-template-columns: 280px minmax(0, 1fr); }
    .sidebar { background: #0b2730; color: #e8f5f8; padding: 24px; display: flex; flex-direction: column; gap: 28px; }
    .brand { display: flex; gap: 14px; align-items: center; }
    .logo { width: 46px; height: 46px; border-radius: 14px; background: #d1f7ff; color: #0b5c70; display: grid; place-items: center; font-weight: 900; }
    .brand strong { display: block; font-size: 17px; }
    .brand span { display: block; color: #a6c5ce; font-size: 13px; margin-top: 3px; }
    nav { display: grid; gap: 8px; }
    nav a { padding: 12px 13px; border-radius: 12px; color: #d7edf2; font-weight: 750; }
    nav a:hover, nav a.active { background: rgba(255,255,255,.12); color: white; }
    .sidebar-footer { margin-top: auto; display: grid; gap: 12px; }
    main { min-width: 0; }
    .topbar { height: 72px; background: white; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; padding: 0 28px; }
    .topbar strong, .topbar span { display: block; }
    .topbar span { color: var(--muted); font-size: 13px; margin-top: 3px; }
    .content { padding: 28px; }
    @media (max-width: 900px) {
      .shell { grid-template-columns: 1fr; }
      .sidebar { position: static; }
      .topbar { height: auto; padding: 18px; gap: 12px; align-items: flex-start; flex-direction: column; }
      .content { padding: 18px; }
    }
  `]
})
export class ShellComponent {
  constructor(readonly auth: AuthService) {}
}
