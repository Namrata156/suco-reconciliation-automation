/**
 * Roles switched off (plan B, owner 2026-11-12): /me roles_enforced false
 * hides the role names, the role column and the role filters. A /me without
 * the field (an older server) keeps them.
 */
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Me, UserActivityReportRow, UserRoleRow } from '../api/types';
import { MeProvider } from '../auth/auth';
import { AppLayout } from '../layout/AppLayout';
import { rolesEnforced } from '../lib/roles';
import { makeMe, renderWithProviders, routeFetch } from '../test/fixtures';
import { exportBlock, reportBlock } from '../test/reportFixtures';
import { UserActivityReportPage } from './UserActivityReportPage';
import { UsersPage } from './UsersPage';

const ALL = ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'] as const;
const OFF: Partial<Me> = { roles_enforced: false };

const page = <T,>(results: T[], extra: object = {}) => ({
  count: results.length,
  next: null,
  previous: null,
  results,
  ...extra,
});

const urlsOf = (fetch: ReturnType<typeof routeFetch>) => fetch.mock.calls.map((c) => String(c[0]));

function renderAt(pattern: string, route: string, element: ReactElement, over: Partial<Me>) {
  return renderWithProviders(
    <MeProvider me={makeMe([...ALL], over)}>
      <Routes>
        <Route path={pattern} element={element} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('rolesEnforced', () => {
  it('is false only when the server says so', () => {
    expect(rolesEnforced({ roles_enforced: false })).toBe(false);
    expect(rolesEnforced({ roles_enforced: true })).toBe(true);
    expect(rolesEnforced({})).toBe(true);
    expect(rolesEnforced(undefined)).toBe(true);
  });
});

describe('header user menu', () => {
  const renderHeader = (over: Partial<Me>) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    return renderWithProviders(
      <MeProvider me={makeMe(['Maker', 'Checker'], over)}>
        <Routes>
          <Route path=":module/*" element={<AppLayout />} />
        </Routes>
      </MeProvider>,
      { route: '/upi/cases' },
    );
  };

  it('roles off: the name only, and "Signed in as" in the menu', async () => {
    renderHeader(OFF);
    const button = screen.getByRole('button', { name: 'User menu for Maker One' });
    expect(within(button).getByText('Maker One')).toBeTruthy();
    expect(within(button).queryByText('Maker, Checker')).toBeNull();
    await userEvent.click(button);
    expect(await screen.findByText('Signed in as maker1')).toBeTruthy();
  });

  it('field missing (older server): the roles stay under the name', () => {
    renderHeader({});
    const button = screen.getByRole('button', { name: 'User menu for Maker One' });
    expect(within(button).getByText('Maker, Checker')).toBeTruthy();
  });
});

const ROW: UserRoleRow = {
  id: 2,
  username: 'ops2',
  full_name: 'Ops Two',
  roles: [],
  is_superuser: false,
  is_active: true,
  last_login: null,
};

describe('users page', () => {
  it('roles off: no Role filter, no Roles column, a stale ?role= is not sent', async () => {
    const fetch = routeFetch({ '/users-and-roles/': page([ROW]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/users', '/users?role=Maker', <UsersPage />, OFF);
    const table = await screen.findByRole('table', { name: 'Users and roles' });
    expect(await within(table).findByText('ops2')).toBeTruthy();
    expect(within(table).queryByRole('columnheader', { name: 'Roles' })).toBeNull();
    expect(within(table).queryByText('No role')).toBeNull();
    expect(screen.queryByRole('textbox', { name: 'Role' })).toBeNull();
    expect(screen.getByRole('heading', { name: 'Users' })).toBeTruthy();
    expect(urlsOf(fetch)[0]).not.toContain('role=');
  });

  it('field missing: the Role filter and the Roles column stay', async () => {
    const fetch = routeFetch({ '/users-and-roles/': page([ROW]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/users', '/users?role=Maker', <UsersPage />, {});
    const table = await screen.findByRole('table', { name: 'Users and roles' });
    expect(await within(table).findByText('No role')).toBeTruthy();
    expect(within(table).getByRole('columnheader', { name: 'Roles' })).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Role' })).toBeTruthy();
    expect(urlsOf(fetch)[0]).toContain('role=Maker');
  });
});

const UA_ROW: UserActivityReportRow = {
  user_id: 10,
  username: 'suco',
  full_name: 'suco',
  roles: ['Administrator', 'Checker'],
  is_active: true,
  logins: 22,
  failed_logins: 0,
  last_login: null,
  last_login_ip: null,
  cases: { taken: 0, submitted: 0, approved: 0, rejected: 0, marked_done: 0, remarks: 0 },
  files: { uploaded: 0, refused: 0 },
  cbs_dates_selected: 0,
  runs_started: 0,
  settings: { proposed: 0, decided: 0 },
  report_downloads: 0,
  links: { case_events: '', user_activity: '', operations: '' },
};

describe('user activity report', () => {
  const renderReport = (over: Partial<Me>) => {
    const fetch = routeFetch({
      '/reports/user-activity/': page([UA_ROW], {
        report: reportBlock('user_activity', { module: null }),
        export: exportBlock([['users', 'Users', 1]]),
      }),
    });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/reports/user-activity',
      '/reports/user-activity?date_from=2026-10-15&date_to=2026-11-09&role=Maker',
      <UserActivityReportPage />,
      over,
    );
    return fetch;
  };

  it('roles off: no Role filter, no role line per user, a stale ?role= is not sent', async () => {
    const fetch = renderReport(OFF);
    const table = await screen.findByRole('table', { name: 'Activity per user' });
    expect(await within(table).findByText('22')).toBeTruthy();
    expect(within(table).queryByText('Administrator, Checker')).toBeNull();
    expect(screen.queryByRole('textbox', { name: 'Role' })).toBeNull();
    expect(urlsOf(fetch).find((u) => u.includes('/reports/user-activity/'))).not.toContain('role=');
  });

  it('field missing: the Role filter and the role line stay', async () => {
    const fetch = renderReport({});
    const table = await screen.findByRole('table', { name: 'Activity per user' });
    expect(await within(table).findByText('Administrator, Checker')).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Role' })).toBeTruthy();
    expect(urlsOf(fetch).find((u) => u.includes('/reports/user-activity/'))).toContain('role=Maker');
  });
});
