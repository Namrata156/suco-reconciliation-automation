/**
 * The ONE place the SPA talks to Django. Every endpoint is a typed function
 * here; screens never call fetch() themselves.
 *
 * Auth (design 2.2 / 7.1): Django's session cookie (HttpOnly, sent by the
 * browser) + CSRF. Writes send the csrftoken cookie back in X-CSRFToken.
 * No token is ever kept in browser storage.
 *
 * Session timeout (design 7.2 / P7): the server renews the 15-minute idle
 * session on each request, so the time of the last request is the clock the
 * timeout warning follows (see sessionClock below).
 */
import type {
  ApiErrorBody,
  BulkName,
  BulkPayloads,
  BulkResult,
  CaseDetail,
  CaseEventInfo,
  CaseFilters,
  CaseListItem,
  Me,
  ModuleMeta,
  ModuleSummary,
  Paginated,
  PaymentModuleRef,
  TransitionName,
  TransitionPayloads,
  UserRef,
} from './types';

export const API_ROOT = '/api/v1';

// ---------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly fieldErrors: Record<string, string[]>;
  /** Cases whose approval needs confirm_self_approval (400 on approve / bulk approve). */
  readonly selfApprovalCaseIds: number[];

  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    const detail = body?.detail || fallback;
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.fieldErrors = body?.errors ?? {};
    this.selfApprovalCaseIds = body?.self_approval_case_ids ?? [];
  }

  /** The server refused an approval because it must be confirmed as self-approved. */
  get needsSelfApprovalConfirmation(): boolean {
    return this.status === 400 && 'confirm_self_approval' in this.fieldErrors;
  }

  /** 401: not logged in / session expired (v1, design 3.4). */
  get isUnauthenticated(): boolean {
    return this.status === 401;
  }
  /** 403: the user's role may not do this. */
  get isForbidden(): boolean {
    return this.status === 403;
  }
  /** 409: the item is in the wrong status (someone else acted first). */
  get isConflict(): boolean {
    return this.status === 409;
  }
}

const STATUS_TEXT: Record<number, string> = {
  400: 'The request was not accepted.',
  401: 'Your session has ended. Please sign in again.',
  403: 'Your role may not do this.',
  404: 'Not found.',
  409: 'Someone else changed this item first.',
};

// ---------------------------------------------------------------------------
// session clock + "session ended" signal
// ---------------------------------------------------------------------------
type Listener = () => void;

function createSessionClock() {
  let lastRequestAt = Date.now();
  const activity = new Set<Listener>();
  const expired = new Set<Listener>();
  return {
    lastRequestAt: () => lastRequestAt,
    touch(at = Date.now()) {
      lastRequestAt = at;
      activity.forEach((l) => l());
    },
    onActivity(l: Listener) {
      activity.add(l);
      return () => void activity.delete(l);
    },
    /** Fired when any call (other than the login probe) answers 401. */
    onExpired(l: Listener) {
      expired.add(l);
      return () => void expired.delete(l);
    },
    expire() {
      expired.forEach((l) => l());
    },
  };
}

export const sessionClock = createSessionClock();

// ---------------------------------------------------------------------------
// low-level request
// ---------------------------------------------------------------------------
export function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(';')) {
    const c = part.trim();
    if (c.startsWith(prefix)) return decodeURIComponent(c.slice(prefix.length));
  }
  return null;
}

type Query = Record<string, string | number | boolean | undefined | null>;

export function toQueryString(query?: Query): string {
  if (!query) return '';
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === '') continue;
    p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : '';
}

interface RequestOptions {
  query?: Query;
  body?: unknown;
  /** Do not raise the global "session ended" signal on 401 (login probe). */
  quiet401?: boolean;
}

async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const unsafe = method !== 'GET' && method !== 'HEAD';
  if (unsafe) {
    headers['Content-Type'] = 'application/json';
    const token = readCookie('csrftoken');
    if (token) headers['X-CSRFToken'] = token;
  }
  const res = await fetch(`${path}${toQueryString(opts.query)}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: unsafe && opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });

  if (res.status === 401) {
    if (!opts.quiet401) sessionClock.expire();
  } else {
    // Any answered request renews the server's idle timer.
    sessionClock.touch();
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    throw new ApiError(
      res.status,
      (data as ApiErrorBody | null) ?? null,
      STATUS_TEXT[res.status] ?? `Request failed (${res.status}).`,
    );
  }
  return data as T;
}

/** Accept a plain list or a paginated {results} (list endpoints not yet built). */
function unwrapList<T>(data: T[] | Paginated<T>): T[] {
  return Array.isArray(data) ? data : data.results;
}

const mod = (module: string) => `${API_ROOT}/${encodeURIComponent(module.toLowerCase())}`;

// ---------------------------------------------------------------------------
// auth (design 4.1 - TO BUILD)
// ---------------------------------------------------------------------------
/** Sets the csrftoken cookie before the first write (login). */
export function fetchCsrf(): Promise<void> {
  return request<void>('GET', `${API_ROOT}/auth/csrf/`, { quiet401: true });
}

/**
 * Login is CSRF-checked too, so a fresh token is fetched first. Django
 * rotates the token on login; every later write re-reads the cookie.
 * Answers: 200 = Me; 400 "Invalid credentials" / missing fields; 403 = valid
 * password but no role in this system.
 */
export async function login(username: string, password: string): Promise<Me> {
  await fetchCsrf();
  return request<Me>('POST', `${API_ROOT}/auth/login/`, { body: { username, password }, quiet401: true });
}

/** Always 204 (also for an already-expired session). */
export function logout(): Promise<void> {
  return request<void>('POST', `${API_ROOT}/auth/logout/`, { quiet401: true });
}

/** The signed-in user, or null when there is no session. */
export async function getMe(): Promise<Me | null> {
  try {
    return await request<Me>('GET', `${API_ROOT}/me/`, { quiet401: true });
  } catch (e) {
    if (e instanceof ApiError && e.isUnauthenticated) return null;
    throw e;
  }
}

/** Renews the session ("Stay signed in"). Raises the expiry signal on 401. */
export function keepAlive(): Promise<Me> {
  return request<Me>('GET', `${API_ROOT}/me/`);
}

// ---------------------------------------------------------------------------
// reference data
// ---------------------------------------------------------------------------
export async function getModules(): Promise<PaymentModuleRef[]> {
  return unwrapList(
    await request<PaymentModuleRef[] | Paginated<PaymentModuleRef>>('GET', `${API_ROOT}/modules/`),
  );
}

export function getMeta(module: string): Promise<ModuleMeta> {
  return request<ModuleMeta>('GET', `${mod(module)}/meta/`);
}

export function getSummary(module: string): Promise<ModuleSummary> {
  return request<ModuleSummary>('GET', `${mod(module)}/summary/`);
}

export async function listUsers(role: string): Promise<UserRef[]> {
  return unwrapList(
    await request<UserRef[] | Paginated<UserRef>>('GET', `${API_ROOT}/users/`, { query: { role } }),
  );
}

// ---------------------------------------------------------------------------
// cases (design 3.2 / 4.4-4.7)
// ---------------------------------------------------------------------------
export function listCases(module: string, filters: CaseFilters): Promise<Paginated<CaseListItem>> {
  return request<Paginated<CaseListItem>>('GET', `${mod(module)}/cases/`, { query: { ...filters } });
}

export function getCase(module: string, id: number): Promise<CaseDetail> {
  return request<CaseDetail>('GET', `${mod(module)}/cases/${id}/`);
}

/** The case's events, paginated; oldest first unless ordering="-created_at". */
export function getCaseHistory(
  module: string,
  id: number,
  opts: { page?: number; ordering?: 'created_at' | '-created_at' } = {},
): Promise<Paginated<CaseEventInfo>> {
  return request<Paginated<CaseEventInfo>>('GET', `${mod(module)}/cases/${id}/history/`, {
    query: { ...opts },
  });
}

export function caseTransition<N extends TransitionName>(
  module: string,
  id: number,
  name: N,
  payload: TransitionPayloads[N],
): Promise<CaseDetail> {
  return request<CaseDetail>('POST', `${mod(module)}/cases/${id}/${name}/`, { body: payload });
}

export function bulkAction<N extends BulkName>(
  module: string,
  name: N,
  payload: BulkPayloads[N],
): Promise<BulkResult> {
  return request<BulkResult>('POST', `${mod(module)}/cases/bulk/${name}/`, { body: payload });
}
