/** API client for Nodeglow backend */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? '';

function getCsrfToken(): string {
  if (typeof document === 'undefined') return '';
  const match = document.cookie.match(/ng_csrf=([^;]+)/);
  if (!match) return '';
  return decodeURIComponent(match[1]).split('.')[0] ?? '';
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public data?: unknown) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(options.headers as Record<string, string>),
  };

  const method = (options.method ?? 'GET').toUpperCase();
  if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
    headers['x-csrf-token'] = getCsrfToken();
    if (options.body instanceof URLSearchParams) {
      headers['Content-Type'] = 'application/x-www-form-urlencoded';
    } else if (options.body && typeof options.body === 'string') {
      headers['Content-Type'] = 'application/json';
    }
  }

  const res = await fetch(url, {
    ...options,
    headers,
    credentials: 'include',
  });

  if (res.status === 401) {
    if (typeof window !== 'undefined') {
      window.location.href = '/login';
    }
    throw new ApiError(401, 'Unauthorized');
  }

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(res.status, `API ${method} ${path}: ${res.status}`, text);
  }

  const contentType = res.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    return res.json() as Promise<T>;
  }
  return res.text() as unknown as T;
}

/** Convenience methods */
export const get = <T>(path: string) => api<T>(path);

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: 'POST',
    body: body ? JSON.stringify(body) : undefined,
  });

export const patch = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: 'PATCH',
    body: body ? JSON.stringify(body) : undefined,
  });

export const del = <T>(path: string) =>
  api<T>(path, { method: 'DELETE' });
