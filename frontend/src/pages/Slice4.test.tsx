/**
 * Slice 4: alerts bell and list, audit lists, users and roles, and the
 * Slice 3 fit items (docs/phase5_slice4_api_contract.txt).
 */
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type {
  AuditCaseEvent,
  AuditMeta,
  AuditUserActivity as ActivityRow,
  CodeChangeRequest,
  RoleName,
  UserAlert,
} from '../api/types';
import { makeQueryClient, Providers } from '../App';
import { MeProvider } from '../auth/auth';
import { AlertsBell, bellLabel } from '../components/AlertsBell';
import { requestTarget } from '../features/settings/ResponseCodesTab';
import { NtslMappingTab } from '../features/settings/NtslMappingTab';
import { ALERTS_POLL, POLL } from '../lib/polling';
import { spaLink } from '../lib/links';
import { json, makeMe, META, renderWithProviders, routeFetch, sentBody } from '../test/fixtures';
import { AlertsPage } from './AlertsPage';
import { AuditCaseEvents, AuditPage, AuditUserActivity } from './AuditPage';
import { PendingApprovalsPage } from './PendingApprovalsPage';
import { UsersPage } from './UsersPage';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const page = <T,>(results: T[], extra: object = {}) => ({
  count: results.length,
  next: null,
  previous: null,
  results,
  ...extra,
});

const isPost = (init: RequestInit | undefined) => init?.method === 'POST';
const urlsOf = (fetch: ReturnType<typeof routeFetch>) => fetch.mock.calls.map((c) => String(c[0]));

function renderAs(roles: RoleName[], path: string, route: string, element: ReactElement) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

// ---------------------------------------------------------------------------
// links
// ---------------------------------------------------------------------------
describe('server links (contract A / B "link")', () => {
  it('follows SPA addresses only', () => {
    expect(spaLink('/upi/cases/123')).toBe('/upi/cases/123');
    expect(spaLink('/upi/cases?mine=approval')).toBe('/upi/cases?mine=approval');
    expect(spaLink(null)).toBeNull();
    expect(spaLink('https://evil.example/x')).toBeNull();
    expect(spaLink('//evil.example/x')).toBeNull();
    expect(spaLink('/\\evil')).toBeNull();
    // Older alerts mailed Django-admin URLs; never an SPA link.
    expect(spaLink('/admin/exception_management/exceptioncase/5/change/')).toBeNull();
    expect(spaLink('/api/v1/me/')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// bell
// ---------------------------------------------------------------------------
/** Advance the fake clock in 5 s steps, so React re-renders between them (as in a browser). */
async function advance(ms: number) {
  for (let t = 0; t < ms; t += POLL.everyMs) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(Math.min(POLL.everyMs, ms - t));
    });
  }
}

function renderBell() {
  const client = makeQueryClient();
  return render(
    <Providers client={client}>
      <MemoryRouter>
        <AlertsBell />
      </MemoryRouter>
    </Providers>,
  );
}

describe('alerts bell (design 4.2 / 4.18)', () => {
  it('names the unread count', () => {
    expect(bellLabel(undefined)).toBe('Alerts');
    expect(bellLabel(0)).toBe('Alerts, none unread');
    expect(bellLabel(3)).toBe('Alerts, 3 unread');
  });

  it('shows the unread count and links to the alerts list', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/notifications/unread-count/': { unread: 3, latest_id: 9 } }));
    renderBell();
    const bell = await screen.findByRole('link', { name: 'Alerts, 3 unread' });
    expect(bell.getAttribute('href')).toBe('/alerts');
  });

  it('polls every 60 s while the user is active and stops when idle, so it never keeps the session open', async () => {
    expect(ALERTS_POLL.everyMs).toBe(60_000);
    expect(ALERTS_POLL.stopAfterIdleMs).toBe(POLL.stopAfterIdleMs);
    vi.useFakeTimers();
    const fetch = routeFetch({ '/notifications/unread-count/': { unread: 1, latest_id: 2 } });
    vi.stubGlobal('fetch', fetch);
    renderBell();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(fetch).toHaveBeenCalledTimes(1);

    // Active: one more request a minute.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ALERTS_POLL.everyMs);
    });
    expect(fetch).toHaveBeenCalledTimes(2);

    // No input for 20 minutes: at most one more request, then nothing.
    await advance(20 * 60_000);
    const idleCalls = fetch.mock.calls.length;
    expect(idleCalls).toBeLessThanOrEqual(3);
    await advance(10 * 60_000);
    expect(fetch).toHaveBeenCalledTimes(idleCalls);

    // The user is back: polling resumes.
    act(() => {
      window.dispatchEvent(new Event('keydown'));
    });
    await advance(ALERTS_POLL.everyMs + POLL.everyMs);
    expect(fetch.mock.calls.length).toBeGreaterThan(idleCalls);
  });
});

// ---------------------------------------------------------------------------
// alerts list
// ---------------------------------------------------------------------------
const alert = (o: Partial<UserAlert> = {}): UserAlert => ({
  id: 7,
  alert_type: 'SUBMITTED',
  alert_type_label: 'Case submitted for approval',
  subject: 'Case 123 awaits your approval',
  body: 'Case 123 (R01) was submitted by maker1.\nhttp://localhost:8000/admin/exception_management/exceptioncase/123/change/',
  created_at: '2026-10-20T04:30:00Z',
  read: false,
  read_at: null,
  module: 'upi',
  link: '/upi/cases/123',
  related: { type: 'case', id: 123 },
  ...o,
});

describe('alerts list (design 4.18, contract A)', () => {
  it('lists my alerts with SPA links, marks one read, and marks all read up to the newest shown', async () => {
    const fetch = routeFetch({
      '/notifications/': page(
        [
          alert({ id: 9 }),
          alert({ id: 8, subject: 'Digest', alert_type: 'DIGEST_MAKER', link: null }),
          alert({ id: 4, read: true, read_at: '2026-10-19T04:30:00Z', link: '/admin/x/' }),
        ],
        { unread: 2 },
      ),
      '/notifications/9/read/': alert({ id: 9, read: true }),
      '/notifications/read-all/': { marked: 2, unread: 0 },
    });
    vi.stubGlobal('fetch', fetch);
    renderAs(['Maker'], '/alerts', '/alerts', <AlertsPage />);

    const table = await screen.findByRole('table', { name: 'My alerts' });
    expect(await screen.findByText(/3 alert\(s\), 2 unread/)).toBeTruthy();
    const link = within(table).getByRole('link', { name: 'Case 123 awaits your approval (open alert 9)' });
    expect(link.getAttribute('href')).toBe('/upi/cases/123');
    // No link: null, and an admin URL is never followed.
    expect(within(table).queryByRole('link', { name: /open alert 8/ })).toBeNull();
    expect(within(table).queryByRole('link', { name: /open alert 4/ })).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: 'Mark alert 9 read' }));
    await waitFor(() => expect(urlsOf(fetch)).toContain('/api/v1/notifications/9/read/'));

    await userEvent.click(screen.getByRole('button', { name: 'Mark all read' }));
    await waitFor(() => expect(urlsOf(fetch)).toContain('/api/v1/notifications/read-all/'));
    const post = fetch.mock.calls.find((c) => String(c[0]).endsWith('/read-all/'));
    expect(isPost(post?.[1] as RequestInit)).toBe(true);
    expect(sentBody(post)).toEqual({ up_to_id: 9 });
  });

  it('keeps "unread only" in the address and sends it to the server', async () => {
    const fetch = routeFetch({ '/notifications/': page([], { unread: 0 }) });
    vi.stubGlobal('fetch', fetch);
    renderAs(['Checker'], '/alerts', '/alerts?unread=true', <AlertsPage />);
    expect(await screen.findByText('No unread alerts.')).toBeTruthy();
    expect(urlsOf(fetch).some((u) => u.includes('unread=true'))).toBe(true);
    expect(screen.getByRole('button', { name: 'Mark all read' })).toHaveProperty('disabled', true);
  });
});

// ---------------------------------------------------------------------------
// audit
// ---------------------------------------------------------------------------
const AUDIT_META: AuditMeta = {
  modules: [{ code: 'UPI', slug: 'upi', name: 'Unified Payments Interface' }],
  case_event_types: [
    { value: 'SUBMITTED', label: 'Submitted' },
    { value: 'APPROVED', label: 'Approved' },
  ],
  settings_kinds: META.settings_kinds,
  settings_event_types: [{ value: 'PROPOSED', label: 'Proposed' }],
  user_activity_types: [
    { value: 'LOGIN', label: 'Login' },
    { value: 'LOGIN_FAILED', label: 'Failed login' },
  ],
  operation_types: [{ value: 'RUN_STARTED', label: 'Run started' }],
  orderings: ['-created_at', 'created_at'],
};

const caseEvent = (o: Partial<AuditCaseEvent> = {}): AuditCaseEvent => ({
  id: 501,
  created_at: '2026-10-20T05:00:00Z',
  module: 'upi',
  case_id: 123,
  business_date: '2026-07-14',
  rrn: '419512345678',
  event_type: 'APPROVED',
  event_type_label: 'Approved',
  actor_username: 'checker1',
  source: 'api',
  old_status: 'PENDING_APPROVAL',
  new_status: 'APPROVED',
  remarks: 'ok',
  changes: {},
  action_snapshot: { action_type: 'DRC_102' },
  self_approved: true,
  link: '/upi/cases/123',
  ...o,
});

const activity = (o: Partial<ActivityRow> = {}): ActivityRow => ({
  id: 77,
  created_at: '2026-10-20T03:00:00Z',
  event_type: 'LOGIN',
  event_type_label: 'Login',
  username: 'auditor1',
  user_id: 3,
  ip_address: '10.0.0.5',
  user_agent: 'Edge',
  path: '/api/v1/auth/login/',
  ...o,
});

function renderAudit(roles: RoleName[], route: string) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path="/audit" element={<AuditPage />}>
          <Route path="case-events" element={<AuditCaseEvents />} />
          <Route path="user-activity" element={<AuditUserActivity />} />
        </Route>
      </Routes>
    </MeProvider>,
    { route },
  );
}

describe('audit trail (design 4.19, contract B)', () => {
  it("Auditor: the day's case events, filters from the address, read-only", async () => {
    const fetch = routeFetch({
      '/audit/meta/': AUDIT_META,
      '/audit/case-events/': page([
        caseEvent(),
        caseEvent({ id: 500, event_type_label: 'Submitted', self_approved: false }),
      ]),
    });
    vi.stubGlobal('fetch', fetch);
    renderAudit(['Auditor'], '/audit/case-events?date_from=2026-10-20&date_to=2026-10-20&user=checker1');

    const table = await screen.findByRole('table', { name: 'Case events' });
    expect(await within(table).findByRole('link', { name: 'Case 123 (event 501)' })).toBeTruthy();
    expect(within(table).getAllByText('PENDING_APPROVAL -> APPROVED')).toHaveLength(2);
    expect(within(table).getAllByText(/Action: action_type: DRC_102/).length).toBeGreaterThan(0);
    const call = urlsOf(fetch).find((u) => u.includes('/audit/case-events/')) ?? '';
    expect(call).toContain('date_from=2026-10-20');
    expect(call).toContain('date_to=2026-10-20');
    expect(call).toContain('user=checker1');
    // The text filter shows the address's value.
    expect(screen.getByRole('textbox', { name: 'User name' })).toHaveProperty('value', 'checker1');

    // Nothing here changes anything: only GETs, and no action buttons.
    expect(fetch.mock.calls.every((c) => !isPost(c[1] as RequestInit))).toBe(true);
    const buttons = screen
      .getAllByRole('button')
      .map((b) => b.textContent?.trim() || b.getAttribute('aria-label'));
    for (const b of buttons)
      expect(b ?? '').not.toMatch(/approve|reject|submit|take|delete|save|propose|mark/i);
  });

  it('applies the user name filter on Enter and goes back to page 1', async () => {
    const fetch = routeFetch({ '/audit/meta/': AUDIT_META, '/audit/case-events/': page([caseEvent()]) });
    vi.stubGlobal('fetch', fetch);
    renderAudit(['Administrator'], '/audit/case-events?page=3');
    const box = await screen.findByRole('textbox', { name: 'User name' });
    await userEvent.type(box, 'maker1{Enter}');
    await waitFor(() => expect(urlsOf(fetch).some((u) => u.includes('user=maker1'))).toBe(true));
    const last =
      urlsOf(fetch)
        .filter((u) => u.includes('/audit/case-events/'))
        .at(-1) ?? '';
    expect(last).not.toContain('page=3');
  });

  it('Auditor: the login log', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/audit/meta/': AUDIT_META,
        '/audit/user-activity/': page([
          activity(),
          activity({
            id: 78,
            event_type: 'LOGIN_FAILED',
            event_type_label: 'Failed login',
            username: 'nobody',
            user_id: null,
          }),
        ]),
      }),
    );
    renderAudit(['Auditor'], '/audit/user-activity');
    const table = await screen.findByRole('table', { name: 'Login log' });
    expect(await within(table).findByText('Failed login')).toBeTruthy();
    expect(within(table).getAllByText('10.0.0.5', { selector: 'td' })).toHaveLength(2);
    expect(within(table).getByText('no matching account')).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Login log', selected: true })).toBeTruthy();
  });

  it('a Maker is told the audit trail is not for their role, and nothing is fetched', async () => {
    const fetch = routeFetch({});
    vi.stubGlobal('fetch', fetch);
    renderAudit(['Maker'], '/audit/case-events');
    expect(await screen.findByText(/Only an Administrator or an Auditor/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('shows a 403 from the server as a role message', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/audit/meta/': AUDIT_META,
        '/audit/case-events/': () => json(403, { detail: 'You do not have permission.' }),
      }),
    );
    renderAudit(['Auditor'], '/audit/case-events');
    expect(await screen.findByText('Your role may not read the audit trail.')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// users and roles
// ---------------------------------------------------------------------------
describe('users and roles (design 4.17, contract C1)', () => {
  it('Administrator: read-only list and the Django admin link', async () => {
    const fetch = routeFetch({
      '/users-and-roles/': page(
        [
          {
            id: 1,
            username: 'admin',
            full_name: 'Admin',
            roles: ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'],
            is_superuser: true,
            is_active: true,
            last_login: null,
          },
          {
            id: 2,
            username: 'old',
            full_name: '',
            roles: [],
            is_superuser: false,
            is_active: false,
            last_login: '2026-01-01T00:00:00Z',
          },
        ],
        { admin_users_url: '/admin/auth/user/' },
      ),
    });
    vi.stubGlobal('fetch', fetch);
    renderAs(['Administrator'], '/users', '/users?role=Maker', <UsersPage />);
    const table = await screen.findByRole('table', { name: 'Users and roles' });
    expect(await within(table).findByText('Superuser')).toBeTruthy();
    expect(within(table).getByText('No role')).toBeTruthy();
    expect(within(table).getByText('Never')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Edit users in the Django admin' }).getAttribute('href')).toBe(
      '/admin/auth/user/',
    );
    expect(urlsOf(fetch)[0]).toContain('role=Maker');
  });

  it('Auditor: not given the list (design 4.17), nothing fetched', async () => {
    const fetch = routeFetch({});
    vi.stubGlobal('fetch', fetch);
    renderAs(['Auditor'], '/users', '/users', <UsersPage />);
    expect(await screen.findByText(/Only an Administrator sees the users and roles/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Slice 3 fit items (contract D)
// ---------------------------------------------------------------------------
describe('Slice 3 fit items (contract D)', () => {
  const req = (o: Partial<CodeChangeRequest>): CodeChangeRequest => ({
    id: 5,
    kind: 'EDIT',
    flag: 'RET',
    response_code_id: 11,
    changes: {},
    new_source: '',
    reason: 'r',
    status: 'PENDING',
    requested_by: 'maker1',
    requested_at: '2026-10-20T05:00:00Z',
    decided_by: null,
    decided_at: null,
    decision_remarks: '',
    self_approved: false,
    self_approval: false,
    response_code_label: 'RET 115 - Beneficiary account closed',
    ...o,
  });

  it('D2: a request names its code with the server label, no "Code #id" lookup', () => {
    expect(requestTarget(req({}))).toBe('RET 115 - Beneficiary account closed');
    expect(requestTarget(req({ kind: 'CREATE', response_code_id: null, response_code_label: null }))).toBe(
      'New RET code',
    );
  });

  it('D1: the NTSL match and txn types come from /meta/', async () => {
    const meta = {
      ...META,
      ntsl_match_types: [
        { value: 'EXACT', label: 'Exact (from meta)' },
        { value: 'REGEX', label: 'Regex (from meta)' },
      ],
    };
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/meta/': meta,
        '/settings/ntsl-mapping/': {
          active: {
            id: 1,
            version: 1,
            status: 'ACTIVE',
            based_on_version: null,
            reason: '',
            proposed_by: null,
            proposed_at: null,
            decided_by: null,
            decided_at: null,
            decision_remarks: '',
            self_approved: false,
            activated_at: null,
            retired_at: null,
            entry_count: 0,
            change_counts: { added: 0, removed: 0, recategorised_loaded_descriptions: 0 },
            self_approval: false,
          },
          pending: null,
          categories: META.ntsl_categories,
          can_propose: true,
          can_decide: false,
        },
        '/settings/ntsl-mapping/versions/': page([]),
        '/settings/ntsl-mapping/versions/1/entries/': page([]),
      }),
    );
    renderAs(['Maker'], '/:module/settings/ntsl-mapping', '/upi/settings/ntsl-mapping', <NtslMappingTab />);
    // The entries filter lists the meta values (options of a closed Mantine select are hidden).
    await userEvent.click(await screen.findByRole('textbox', { name: 'Match type' }));
    expect(await screen.findByRole('option', { name: 'Regex (from meta)', hidden: true })).toBeTruthy();
    expect(
      screen.queryByRole('option', { name: 'Regular expression (full match)', hidden: true }),
    ).toBeNull();
  });

  it('D4: the pending list shows the events of a code request', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/meta/': META,
        '/settings/pending/': page(
          [
            {
              kind: 'response_code',
              kind_label: 'Response code change',
              id: 5,
              title: 'EDIT: RET 115',
              reason: 'r',
              proposed_by: 'maker1',
              proposed_at: '2026-10-20T05:00:00Z',
              self_approval: false,
              detail: req({
                events: [
                  {
                    event_type: 'PROPOSED',
                    actor_username: 'maker1',
                    created_at: '2026-10-20T05:00:00Z',
                    remarks: 'circular',
                    self_approved: false,
                  },
                ],
              }),
            },
          ],
          { can_decide: false, cases_awaiting_approval: { count: 0, query: { mine: 'approval' } } },
        ),
      }),
    );
    renderAs(['Auditor'], '/:module/approvals', '/upi/approvals', <PendingApprovalsPage />);
    const history = await screen.findByRole('list', { name: 'History of EDIT: RET 115' });
    expect(within(history).getByText(/PROPOSED by maker1: circular/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// routes and shell
// ---------------------------------------------------------------------------
describe('routes and shell (Slice 4)', () => {
  it('/audit/... opens in the shell (not read as a module), with the bell and the Auditor menu', async () => {
    const { AppRoutes } = await import('../AppRoutes');
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/me/': makeMe(['Auditor']),
        '/notifications/unread-count/': { unread: 2, latest_id: 5 },
        '/audit/meta/': AUDIT_META,
        '/audit/case-events/': page([caseEvent()]),
      }),
    );
    renderWithProviders(<AppRoutes />, { route: '/audit' });
    expect(await screen.findByRole('heading', { name: 'Audit trail' }, { timeout: 5000 })).toBeTruthy();
    expect(await screen.findByRole('table', { name: 'Case events' }, { timeout: 5000 })).toBeTruthy();
    expect(await screen.findByRole('link', { name: 'Alerts, 2 unread' })).toBeTruthy();
    const nav = screen.getByRole('navigation', { name: 'Main menu' });
    const audit = within(nav).getByRole('link', { name: 'Audit trail' });
    expect(audit.getAttribute('href')).toBe('/audit');
    expect(audit.hasAttribute('data-active')).toBe(true);
    expect(within(nav).getByRole('link', { name: 'Exception cases' }).getAttribute('href')).toBe(
      '/upi/cases',
    );
    expect(within(nav).queryByRole('link', { name: 'Users and roles' })).toBeNull();
  });
});
