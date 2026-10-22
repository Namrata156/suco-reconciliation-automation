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
  AlertFilters,
  AlertListPage,
  ApiErrorBody,
  AuditCaseEvent,
  AuditFilters,
  AuditMeta,
  AuditOperation,
  AuditSettingsEvent,
  AuditUserActivity,
  BulkName,
  BulkPayloads,
  BulkResult,
  BusinessDateDetail,
  BusinessDateOverview,
  CaseDetail,
  CaseEventInfo,
  CaseFilters,
  CaseListItem,
  CodeChangeProposal,
  CodeChangeRequest,
  CodeRequestFilters,
  CodeRequestListPage,
  DateFilters,
  DetectResult,
  FileFilters,
  FileListPage,
  FileValidationExceptionInfo,
  GroupDetail,
  MapEntry,
  MapEntryFilters,
  MappingProposal,
  MapVersion,
  MapVersionDetail,
  MarkAllReadResult,
  Me,
  ModuleMeta,
  ModuleSummary,
  NtslMappingState,
  Paginated,
  PaymentModuleRef,
  PendingApprovalsPage,
  ReconciliationRunInfo,
  ResponseCodeFilters,
  ResponseCodeListPage,
  ResultFilters,
  ResultRow,
  ResultSummary,
  SettingsApprovePayload,
  SettingsDecisionResults,
  SettingsKind,
  SettingsRejectPayload,
  SettlementDate,
  SettlementTransactionFilters,
  SettlementTransactionsPage,
  ToleranceDetail,
  ToleranceProposal,
  ToleranceState,
  ToleranceVersion,
  TransitionName,
  TransitionPayloads,
  UnreadAlertCount,
  UploadedFileDetail,
  UploadItem,
  UploadResult,
  UserAlert,
  UserRef,
  UserRoleFilters,
  UserRoleListPage,
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
  /** The whole parsed body (an upload's 400 carries the per-file refusals). */
  readonly body: unknown;

  constructor(status: number, body: ApiErrorBody | null, fallback: string) {
    const detail = body?.detail || fallback;
    super(detail);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.fieldErrors = body?.errors ?? {};
    this.selfApprovalCaseIds = body?.self_approval_case_ids ?? [];
    this.body = body;
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
  // Multipart (file upload): the browser sets Content-Type with the boundary.
  const multipart = opts.body instanceof FormData;
  if (unsafe) {
    if (!multipart) headers['Content-Type'] = 'application/json';
    const token = readCookie('csrftoken');
    if (token) headers['X-CSRFToken'] = token;
  }
  let body: BodyInit | undefined;
  if (unsafe && opts.body !== undefined) body = multipart ? (opts.body as FormData) : JSON.stringify(opts.body);
  const res = await fetch(`${path}${toQueryString(opts.query)}`, {
    method,
    headers,
    credentials: 'same-origin',
    body,
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

// ---------------------------------------------------------------------------
// files, CBS date, business dates and the run (design 4.8-4.11, Slice 2)
// Writes are for the Operations User only; the server refuses others (403).
// ---------------------------------------------------------------------------
/** How much of each file detection needs (intake.DETECT_HEAD_BYTES; detect echoes it). */
export const DETECT_HEAD_BYTES = 64 * 1024;

/**
 * Dry run: only the first DETECT_HEAD_BYTES of each file are sent, plus its
 * SHA-256 ("" when not computed) so the server can name an earlier upload of
 * the same file. Nothing is saved.
 */
export function detectFiles(module: string, files: { file: File; sha256: string }[]): Promise<DetectResult> {
  const form = new FormData();
  for (const { file, sha256 } of files) {
    form.append('files', file.slice(0, DETECT_HEAD_BYTES), file.name);
    form.append('sha256', sha256);
  }
  return request<DetectResult>('POST', `${mod(module)}/files/detect/`, { body: form });
}

function isUploadResult(body: unknown): body is UploadResult {
  return !!body && typeof body === 'object' && 'refused' in body && 'accepted' in body;
}

/**
 * Upload for one business date; each file is accepted or refused on its own.
 * "No file was accepted" (400 with the refusals) is returned, not thrown, as
 * it is a normal outcome; any other error is thrown.
 */
export async function uploadFiles(
  module: string,
  businessDate: string,
  entries: { file: File; item: UploadItem }[],
): Promise<UploadResult> {
  const form = new FormData();
  form.append('business_date', businessDate);
  for (const { file } of entries) form.append('files', file, file.name);
  form.append('items', JSON.stringify(entries.map((e) => e.item)));
  try {
    return await request<UploadResult>('POST', `${mod(module)}/files/`, { body: form });
  } catch (e) {
    if (e instanceof ApiError && e.status === 400 && isUploadResult(e.body)) return e.body;
    throw e;
  }
}

export function listFiles(module: string, filters: FileFilters): Promise<FileListPage> {
  return request<FileListPage>('GET', `${mod(module)}/files/`, { query: { ...filters } });
}

export function getFile(module: string, id: number): Promise<UploadedFileDetail> {
  return request<UploadedFileDetail>('GET', `${mod(module)}/files/${id}/`);
}

export function getFileExceptions(
  module: string,
  id: number,
  opts: { severity?: string; page?: number; page_size?: number } = {},
): Promise<Paginated<FileValidationExceptionInfo>> {
  return request<Paginated<FileValidationExceptionInfo>>('GET', `${mod(module)}/files/${id}/exceptions/`, {
    query: { ...opts },
  });
}

/** CBS: the chosen date must be one of date_selection.detected_dates. Answers the file detail. */
export function selectFileDate(module: string, id: number, businessDate: string): Promise<UploadedFileDetail> {
  return request<UploadedFileDetail>('POST', `${mod(module)}/files/${id}/select-date/`, {
    body: { business_date: businessDate },
  });
}

export function listDates(module: string, filters: DateFilters): Promise<Paginated<BusinessDateOverview>> {
  return request<Paginated<BusinessDateOverview>>('GET', `${mod(module)}/dates/`, { query: { ...filters } });
}

export function getDate(module: string, businessDate: string): Promise<BusinessDateDetail> {
  return request<BusinessDateDetail>('GET', `${mod(module)}/dates/${encodeURIComponent(businessDate)}/`);
}

/** Queues presence -> rules + case sync -> settlement (202). Poll getDate for its progress. */
export function startRun(module: string, businessDate: string): Promise<ReconciliationRunInfo> {
  return request<ReconciliationRunInfo>('POST', `${mod(module)}/dates/${encodeURIComponent(businessDate)}/run/`);
}

// ---------------------------------------------------------------------------
// results and settlement (design 4.12-4.15, Slice 3): read-only, all roles.
// 404 = nothing computed for the date yet.
// ---------------------------------------------------------------------------
const day = (module: string, businessDate: string) => `${mod(module)}/dates/${encodeURIComponent(businessDate)}`;

export function getResultSummary(module: string, businessDate: string): Promise<ResultSummary> {
  return request<ResultSummary>('GET', `${day(module, businessDate)}/results/summary/`);
}

export function listResults(
  module: string,
  businessDate: string,
  filters: ResultFilters,
): Promise<Paginated<ResultRow>> {
  return request<Paginated<ResultRow>>('GET', `${day(module, businessDate)}/results/`, { query: { ...filters } });
}

export function getGroup(module: string, businessDate: string, id: number): Promise<GroupDetail> {
  return request<GroupDetail>('GET', `${day(module, businessDate)}/groups/${id}/`);
}

export function getSettlement(module: string, businessDate: string): Promise<SettlementDate> {
  return request<SettlementDate>('GET', `${day(module, businessDate)}/settlement/`);
}

/** Where the contributing transactions come from: a date's current result, or a settlement case's. */
export type SettlementSource = { businessDate: string } | { caseId: number };

export function listSettlementTransactions(
  module: string,
  source: SettlementSource,
  filters: SettlementTransactionFilters,
): Promise<SettlementTransactionsPage> {
  const path =
    'caseId' in source
      ? `${mod(module)}/cases/${source.caseId}/settlement/transactions/`
      : `${day(module, source.businessDate)}/settlement/transactions/`;
  return request<SettlementTransactionsPage>('GET', path, { query: { ...filters } });
}

// ---------------------------------------------------------------------------
// settings and approvals (design 4.16, Slice 3). The services check the role
// (403), the status (409, someone decided first) and the input (400).
// ---------------------------------------------------------------------------
const settings = (module: string) => `${mod(module)}/settings`;

export function listResponseCodes(module: string, filters: ResponseCodeFilters): Promise<ResponseCodeListPage> {
  return request<ResponseCodeListPage>('GET', `${settings(module)}/response-codes/`, { query: { ...filters } });
}

export function listCodeRequests(module: string, filters: CodeRequestFilters): Promise<CodeRequestListPage> {
  return request<CodeRequestListPage>('GET', `${settings(module)}/response-codes/requests/`, {
    query: { ...filters },
  });
}

/** One request with its events (contract D4). */
export function getCodeRequest(module: string, id: number): Promise<CodeChangeRequest> {
  return request<CodeChangeRequest>('GET', `${settings(module)}/response-codes/requests/${id}/`);
}

/** Maker: 201 with the new PENDING request. */
export function proposeCodeChange(module: string, payload: CodeChangeProposal): Promise<CodeChangeRequest> {
  return request<CodeChangeRequest>('POST', `${settings(module)}/response-codes/requests/`, { body: payload });
}

export function getNtslMapping(module: string): Promise<NtslMappingState> {
  return request<NtslMappingState>('GET', `${settings(module)}/ntsl-mapping/`);
}

/** Maker: 201 with the new PENDING version (refused if a loaded description would be unmapped). */
export function proposeMappingChange(module: string, payload: MappingProposal): Promise<MapVersionDetail> {
  return request<MapVersionDetail>('POST', `${settings(module)}/ntsl-mapping/`, { body: payload });
}

export function listMapVersions(
  module: string,
  filters: { status?: string; page?: number; page_size?: number } = {},
): Promise<Paginated<MapVersion>> {
  return request<Paginated<MapVersion>>('GET', `${settings(module)}/ntsl-mapping/versions/`, {
    query: { ...filters },
  });
}

export function getMapVersion(module: string, id: number): Promise<MapVersionDetail> {
  return request<MapVersionDetail>('GET', `${settings(module)}/ntsl-mapping/versions/${id}/`);
}

export function listMapEntries(module: string, versionId: number, filters: MapEntryFilters): Promise<Paginated<MapEntry>> {
  return request<Paginated<MapEntry>>('GET', `${settings(module)}/ntsl-mapping/versions/${versionId}/entries/`, {
    query: { ...filters },
  });
}

export function getTolerance(module: string): Promise<ToleranceState> {
  return request<ToleranceState>('GET', `${settings(module)}/tolerance/`);
}

/** Administrator: 201 with the new PENDING version. */
export function proposeTolerance(module: string, payload: ToleranceProposal): Promise<ToleranceDetail> {
  return request<ToleranceDetail>('POST', `${settings(module)}/tolerance/`, { body: payload });
}

export function listToleranceVersions(
  module: string,
  filters: { status?: string; page?: number; page_size?: number } = {},
): Promise<Paginated<ToleranceVersion>> {
  return request<Paginated<ToleranceVersion>>('GET', `${settings(module)}/tolerance/versions/`, {
    query: { ...filters },
  });
}

export function listPendingApprovals(
  module: string,
  filters: { kind?: string; page?: number; page_size?: number } = {},
): Promise<PendingApprovalsPage> {
  return request<PendingApprovalsPage>('GET', `${settings(module)}/pending/`, { query: { ...filters } });
}

const DECISION_PATH: Record<SettingsKind, string> = {
  response_code: 'response-codes/requests',
  ntsl_mapping: 'ntsl-mapping',
  tolerance: 'tolerance',
};

/**
 * Checker: approve or reject a settings proposal. Approving your own
 * proposal needs confirm_self_approval: true (else 400 with
 * errors.confirm_self_approval, as for cases); reject needs a reason.
 */
export function decideSetting<K extends SettingsKind>(
  module: string,
  kind: K,
  id: number,
  decision: 'approve',
  payload: SettingsApprovePayload,
): Promise<SettingsDecisionResults[K]>;
export function decideSetting<K extends SettingsKind>(
  module: string,
  kind: K,
  id: number,
  decision: 'reject',
  payload: SettingsRejectPayload,
): Promise<SettingsDecisionResults[K]>;
export function decideSetting<K extends SettingsKind>(
  module: string,
  kind: K,
  id: number,
  decision: 'approve' | 'reject',
  payload: SettingsApprovePayload | SettingsRejectPayload,
): Promise<SettingsDecisionResults[K]> {
  return request<SettingsDecisionResults[K]>('POST', `${settings(module)}/${DECISION_PATH[kind]}/${id}/${decision}/`, {
    body: payload,
  });
}

// ---------------------------------------------------------------------------
// alerts (design 4.18, Slice 4 contract A): the signed-in user's own alerts.
// ---------------------------------------------------------------------------
export function listAlerts(filters: AlertFilters): Promise<AlertListPage> {
  return request<AlertListPage>('GET', `${API_ROOT}/notifications/`, { query: { ...filters } });
}

/** The header bell's count: read-only on the server (A2). */
export async function getUnreadAlertCount(): Promise<number> {
  return (await request<UnreadAlertCount>('GET', `${API_ROOT}/notifications/unread-count/`)).unread;
}

/** Idempotent: an already-read alert keeps its first read_at. */
export function markAlertRead(id: number): Promise<UserAlert> {
  return request<UserAlert>('POST', `${API_ROOT}/notifications/${id}/read/`);
}

/**
 * Marks read the alerts up to upToId (the newest one shown), so one that
 * arrived after the list was loaded stays unread (A4).
 */
export function markAllAlertsRead(upToId?: number): Promise<MarkAllReadResult> {
  return request<MarkAllReadResult>('POST', `${API_ROOT}/notifications/read-all/`, {
    body: upToId === undefined ? {} : { up_to_id: upToId },
  });
}

// ---------------------------------------------------------------------------
// audit (design 4.19 / 3.6, Slice 4 contract B): read-only, Administrator
// and Auditor ("audit.view"); others 403. Module is a filter, not a path.
// ---------------------------------------------------------------------------
export type AuditList = 'case-events' | 'settings-events' | 'user-activity' | 'operations';

export interface AuditRows {
  'case-events': AuditCaseEvent;
  'settings-events': AuditSettingsEvent;
  'user-activity': AuditUserActivity;
  operations: AuditOperation;
}

export function getAuditMeta(): Promise<AuditMeta> {
  return request<AuditMeta>('GET', `${API_ROOT}/audit/meta/`);
}

export function listAudit<L extends AuditList>(list: L, filters: AuditFilters): Promise<Paginated<AuditRows[L]>> {
  return request<Paginated<AuditRows[L]>>('GET', `${API_ROOT}/audit/${list}/`, { query: { ...filters } });
}

// ---------------------------------------------------------------------------
// users and roles (design 4.17, Slice 4 contract C): read-only list for the
// Administrator; editing stays in the Django admin (P3).
// ---------------------------------------------------------------------------
export function listUsersAndRoles(filters: UserRoleFilters): Promise<UserRoleListPage> {
  return request<UserRoleListPage>('GET', `${API_ROOT}/users-and-roles/`, { query: { ...filters } });
}
