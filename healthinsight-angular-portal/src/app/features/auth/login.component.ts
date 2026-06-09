import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AuthService } from '../../core/auth.service';
import { UserRole } from '../../core/models';

@Component({
  selector: 'hi-login',
  standalone: true,
  imports: [FormsModule],
  template: `
    <main class="login-page">
      <section class="login-card card">
        <div class="brand-row">
          <div class="logo">HI</div>
          <div>
            <h1>HealthInsight AI Portal</h1>
            <p>Secure upload, workflow tracking, human review, and audit evidence.</p>
          </div>
        </div>

        <div class="notice">
          Local MVP login only. Replace with Amazon Cognito or your own authenticated API before using real data.
        </div>

        <form (ngSubmit)="submit()">
          <div class="form-row">
            <label for="email">Email</label>
            <input id="email" name="email" type="email" [(ngModel)]="email" autocomplete="email" required>
          </div>

          <div class="form-row">
            <label for="password">Password</label>
            <input id="password" name="password" type="password" [(ngModel)]="password" autocomplete="current-password" required>
          </div>

          <div class="form-row">
            <label for="role">Demo role</label>
            <select id="role" name="role" [(ngModel)]="role">
              <option value="administrator">administrator</option>
              <option value="analyst">analyst</option>
              <option value="clinician-user">clinician-user</option>
              <option value="auditor">auditor</option>
            </select>
          </div>

          @if (error) {
            <p class="error">{{ error }}</p>
          }

          <button class="btn primary full" type="submit">Enter portal</button>
        </form>
      </section>
    </main>
  `,
  styles: [`
    .login-page { min-height: 100vh; display: grid; place-items: center; padding: 24px; background: radial-gradient(circle at top left, #d1f7ff, transparent 30%), var(--bg); }
    .login-card { width: min(520px, 100%); padding: 26px; }
    .brand-row { display: flex; gap: 16px; align-items: center; margin-bottom: 22px; }
    .logo { width: 54px; height: 54px; border-radius: 16px; display: grid; place-items: center; background: var(--primary); color: white; font-weight: 900; font-size: 18px; }
    h1 { margin: 0 0 6px; font-size: 25px; }
    p { margin: 0; color: var(--muted); }
    .full { width: 100%; margin-top: 8px; }
    .error { margin: 0 0 12px; color: var(--danger); font-weight: 700; }
  `]
})
export class LoginComponent {
  email = 'analyst@clinic.org';
  password = 'demo-password';
  role: UserRole = 'analyst';
  error = '';

  constructor(private readonly auth: AuthService) {}

  submit(): void {
    try {
      this.error = '';
      this.auth.login(this.email, this.password, this.role);
    } catch (error) {
      this.error = error instanceof Error ? error.message : 'Login failed.';
    }
  }
}
