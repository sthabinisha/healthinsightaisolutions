import { Injectable, computed, signal } from '@angular/core';
import { Router } from '@angular/router';
import { UserRole, UserSession } from './models';

const SESSION_KEY = 'healthinsight.session';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly sessionSignal = signal<UserSession | null>(this.loadSession());

  readonly session = computed(() => this.sessionSignal());
  readonly isLoggedIn = computed(() => Boolean(this.sessionSignal()?.token));

  constructor(private readonly router: Router) {}

  login(email: string, password: string, role: UserRole): void {
    // MVP placeholder. Replace this with Cognito Hosted UI or your API /auth/login endpoint.
    if (!email || !password) {
      throw new Error('Email and password are required.');
    }

    const session: UserSession = {
      token: 'dev-mock-token',
      name: email.split('@')[0] || 'HealthInsight User',
      email,
      role,
      tenantId: 'demo-clinic-001'
    };

    localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    this.sessionSignal.set(session);
    void this.router.navigateByUrl('/dashboard');
  }

  logout(): void {
    localStorage.removeItem(SESSION_KEY);
    this.sessionSignal.set(null);
    void this.router.navigateByUrl('/login');
  }

  private loadSession(): UserSession | null {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as UserSession;
    } catch {
      localStorage.removeItem(SESSION_KEY);
      return null;
    }
  }
}
