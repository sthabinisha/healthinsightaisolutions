import { HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { AuthService } from './auth.service';

export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const session = inject(AuthService).session();
  if (!session?.token) return next(request);

  const authorizedRequest = request.clone({
    setHeaders: {
      Authorization: `Bearer ${session.token}`,
      'x-tenant-id': session.tenantId
    }
  });

  return next(authorizedRequest);
};
