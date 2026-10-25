/**
 * Phase 6 slice 6.1: Reports page, "Results (all dates)", "Settlement (all
 * dates)", the shared filter bar and the print layout, on the existing
 * GET /api/v1/<module>/dates/ (docs/phase6_api_contract.txt, "Who builds what").
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RoleName } from '../api/types';
import { MeProvider } from '../auth/auth';
import { makeDateOverview, makeMe, makeRun, renderWithProviders, routeFetch } from '../test/fixtures';
import { ALL_DATES_PAGE_SIZE, ResultsAllDatesPage, SettlementAllDatesPage } from './AllDatesPages';
import { ReportsPage } from './ReportsPage';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const page = <T,>(results: T[], count = results.length) => ({ count, next: null, previous: null, results });

/** Shows where a row click went. */
function Where() {
  const l = useLocation();
  return <p data-testid="where">{`${l.pathname}${l.search}`}</p>;
}

function renderAt(route: string, element: ReactElement, roles: RoleName[] = ['Operations User']) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path="/:module/results" element={element} />
        <Route path="/:module/settlement" element={element} />
        <Route path="/:module/reports" element={element} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

const dateCalls = (fetch: ReturnType<typeof routeFetch>) =>
  fetch.mock.calls.map((c) => String(c[0])).filter((u) => u.includes('/dates/'));
const queryOf = (url: string) => new URL(url, 'http://x').searchParams;

const DONE = makeDateOverview({
  business_date: '2026-07-14',
  matrix: { built: true, match_groups: 70969, built_at: '2026-07-15T05:02:00Z' },
  rule_evaluation: {
    id: 3,
    rule_set: 'v3.2',
    started_at: '2026-07-15T05:02:00Z',
    finished_at: '2026-07-15T05:03:00Z',
    group_count: 70969,
    triggered_by: 'ops1',
  },
  cases: { total: 594, active: 120 },
  settlement: {
    id: 8,
    status: 'UNEXPLAINED',
    reasons: ['Cycle 3C: NTSL net differs from CBS net'],
    computed_at: '2026-07-15T05:03:30Z',
    unexplained_amount: '1070.50',
  },
  last_run: makeRun(),
});
const TALLIED = makeDateOverview({
  business_date: '2026-07-15',
  matrix: { built: true, match_groups: 69000, built_at: '2026-07-16T05:02:00Z' },
  cases: { total: 88, active: 0 },
  settlement: {
    id: 9,
    status: 'TALLIED',
    reasons: [],
    computed_at: '2026-07-16T05:03:30Z',
    unexplained_amount: '0.00',
  },
});
// Files loaded, nothing run yet: opens the date's overview, not its results.
const NOT_RUN = makeDateOverview({ business_date: '2026-07-16' });

// ---------------------------------------------------------------------------
describe('Reports page (design 5.2, R2)', () => {
  it.each<[RoleName, string[]]>([
    ['Maker', ['volume', 'exceptions', 'ageing']],
    ['Checker', ['volume', 'exceptions', 'ageing', 'approvals']],
    ['Auditor', ['volume', 'exceptions', 'ageing', 'approvals', 'user-activity']],
  ])('%s: the reports their role may open', (role, later) => {
    renderAt('/upi/reports', <ReportsPage />, [role]);
    const cards = screen.getAllByRole('listitem');
    expect(cards).toHaveLength(2 + later.length);
    // The two all-dates reports open now.
    expect(screen.getByRole('link', { name: /Results \(all dates\)/ }).getAttribute('href')).toBe(
      '/upi/results',
    );
    expect(screen.getByRole('link', { name: /Settlement \(all dates\)/ }).getAttribute('href')).toBe(
      '/upi/settlement',
    );
    // The rest are listed, not linked.
    expect(screen.getAllByText('Not available yet')).toHaveLength(later.length);
    expect(screen.getAllByRole('link')).toHaveLength(2);
    if (!later.includes('approvals')) expect(screen.queryByText('Approval report')).toBeNull();
    if (!later.includes('user-activity')) expect(screen.queryByText('User activity report')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
describe('Results (all dates) on GET /dates/', () => {
  it('asks for the range in the address, a month per page, and shows one row per date', async () => {
    const fetch = routeFetch({ '/api/v1/upi/dates/': page([DONE, NOT_RUN]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results?date_from=2026-07-01&date_to=2026-07-31', <ResultsAllDatesPage />);

    const table = await screen.findByRole('table', { name: 'Results by business date' });
    // In the row and in the page total.
    expect(await within(table).findAllByText('70,969')).toHaveLength(2);
    const q = queryOf(dateCalls(fetch)[0]!);
    expect(q.get('date_from')).toBe('2026-07-01');
    expect(q.get('date_to')).toBe('2026-07-31');
    expect(q.get('page_size')).toBe(String(ALL_DATES_PAGE_SIZE));
    expect(q.get('ordering')).toBeNull();

    expect(within(table).getByRole('link', { name: 'Results for 14-Jul-2026' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-14/results',
    );
    expect(within(table).getByRole('link', { name: 'Overview for 16-Jul-2026' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-16',
    );
    expect(within(table).getByText(/v3\.2, 15-Jul-2026 10:33 IST, by ops1/)).toBeTruthy();
    // Totals for the dates on the page (594 + 0 created, 120 + 0 open).
    const foot = within(table).getByRole('rowheader', { name: 'Total, dates on this page' }).closest('tr')!;
    expect(within(foot).getByText('594')).toBeTruthy();
    expect(within(foot).getByText('120')).toBeTruthy();
    expect(screen.getByText('2 business date(s) in the range')).toBeTruthy();
    expect(screen.getByText(/^Figures as of .*refresh for the latest/)).toBeTruthy();
  });

  it('a click anywhere on a row opens the per-date results', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/api/v1/upi/dates/': page([DONE]) }));
    renderAt('/upi/results?date_from=2026-07-01&date_to=2026-07-31', <ResultsAllDatesPage />);
    const cell = await screen.findByText('70,969');
    await userEvent.click(cell);
    expect(screen.getByTestId('where').textContent).toContain('/upi/dates/2026-07-14/results');
  });

  it('the month of the latest business date, up to yesterday, when the address has no dates', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z'));
    const fetch = routeFetch({ '/api/v1/upi/dates/': page([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results', <ResultsAllDatesPage />);
    expect(await screen.findByText(/No business dates in this range/)).toBeTruthy();
    const q = queryOf(dateCalls(fetch)[0]!);
    // 23 Oct: ops are reconciling 22 Oct, so the range ends there, not today.
    expect([q.get('date_from'), q.get('date_to')]).toEqual(['2026-10-01', '2026-10-22']);
  });

  it('on 1 Nov the default and "This month" are October', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-01T06:00:00Z'));
    const fetch = routeFetch({ '/api/v1/upi/dates/': page([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results?date_from=2026-07-01&date_to=2026-07-31', <ResultsAllDatesPage />);
    await screen.findByText(/No business dates in this range/);
    await userEvent.click(screen.getByRole('button', { name: 'This month' }));
    await waitFor(() => {
      const q = queryOf(dateCalls(fetch).at(-1)!);
      expect([q.get('date_from'), q.get('date_to')]).toEqual(['2026-10-01', '2026-10-31']);
    });
  });

  it('the range pickers stop at the latest business date', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z'));
    vi.stubGlobal('fetch', routeFetch({ '/api/v1/upi/dates/': page([]) }));
    renderAt('/upi/results', <ResultsAllDatesPage />);
    await screen.findByText(/No business dates in this range/);
    await userEvent.click(screen.getByLabelText('Business date to'));
    const day = (label: string) =>
      screen.getByRole('button', { name: label, hidden: true }) as HTMLButtonElement;
    await screen.findByRole('button', { name: '22 October 2026', hidden: true });
    expect(day('22 October 2026').disabled).toBe(false);
    expect(day('23 October 2026').disabled).toBe(true);
  });

  it('"Last 7 days" is the 7 business dates ending yesterday', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z'));
    const fetch = routeFetch({ '/api/v1/upi/dates/': page([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results', <ResultsAllDatesPage />);
    await screen.findByText(/No business dates in this range/);
    await userEvent.click(screen.getByRole('button', { name: 'Last 7 days' }));
    await waitFor(() => {
      const q = queryOf(dateCalls(fetch).at(-1)!);
      expect([q.get('date_from'), q.get('date_to')]).toEqual(['2026-10-16', '2026-10-22']);
    });
  });

  it('"Last month" puts the range in the address and reloads', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z'));
    const fetch = routeFetch({ '/api/v1/upi/dates/': page([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results?page=2', <ResultsAllDatesPage />);
    await screen.findByText(/No business dates in this range/);
    await userEvent.click(screen.getByRole('button', { name: 'Last month' }));
    await waitFor(() => {
      const q = queryOf(dateCalls(fetch).at(-1)!);
      expect([q.get('date_from'), q.get('date_to'), q.get('page')]).toEqual([
        '2026-09-01',
        '2026-09-30',
        '1',
      ]);
    });
  });

  it('an over-long range in the address loads nothing and says why', async () => {
    const fetch = routeFetch({ '/api/v1/upi/dates/': page([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results?date_from=2026-01-01&date_to=2026-07-31', <ResultsAllDatesPage />);
    expect(await screen.findByText(/At most 92 days per report/)).toBeTruthy();
    expect(dateCalls(fetch)).toHaveLength(0);
  });

  it('order and page come from the address', async () => {
    const fetch = routeFetch({ '/api/v1/upi/dates/': page([DONE], 40) });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/upi/results?date_from=2026-07-01&date_to=2026-08-31&ordering=business_date&page=2',
      <ResultsAllDatesPage />,
    );
    await screen.findByText('70,969');
    const q = queryOf(dateCalls(fetch)[0]!);
    expect([q.get('ordering'), q.get('page')]).toEqual(['business_date', '2']);
    expect(screen.getByRole('button', { name: 'Dates page 1' })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
describe('Settlement (all dates) on GET /dates/', () => {
  it('status per date, reasons, unexplained amount and a summary of the page', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/api/v1/upi/dates/': page([DONE, TALLIED, NOT_RUN]) }));
    renderAt('/upi/settlement?date_from=2026-07-01&date_to=2026-07-31', <SettlementAllDatesPage />, [
      'Auditor',
    ]);
    const table = await screen.findByRole('table', { name: 'Settlement by business date' });
    await within(table).findByText('Cycle 3C: NTSL net differs from CBS net');
    expect(within(table).getByRole('link', { name: 'Settlement for 14-Jul-2026' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-14/settlement',
    );
    expect(within(table).getByRole('link', { name: 'Overview for 16-Jul-2026' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-16',
    );
    expect(within(table).getByText('not computed')).toBeTruthy();

    const summary = screen.getByRole('group', { name: 'Settlement summary, dates on this page' });
    expect(summary.textContent).toContain('Tallied: 1');
    expect(summary.textContent).toContain('Unexplained: 1');
    expect(summary.textContent).toContain('Not computed: 1');
    expect(summary.textContent).toContain('Unexplained, dates on this page: 1,070.50');
    expect(summary.textContent).not.toContain('Not ready');
  });

  it('a row click opens the per-date settlement', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/api/v1/upi/dates/': page([TALLIED]) }));
    renderAt('/upi/settlement?date_from=2026-07-01&date_to=2026-07-31', <SettlementAllDatesPage />);
    await userEvent.click(await screen.findByText('16-Jul-2026 10:33 IST'));
    expect(screen.getByTestId('where').textContent).toContain('/upi/dates/2026-07-15/settlement');
  });
});

// ---------------------------------------------------------------------------
describe('print layout', () => {
  it('Print opens the browser print; the print header carries the filters, page and user', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => {});
    vi.stubGlobal('fetch', routeFetch({ '/api/v1/upi/dates/': page([DONE], 40) }));
    renderAt(
      '/upi/results?date_from=2026-07-01&date_to=2026-07-31&ordering=business_date',
      <ResultsAllDatesPage />,
    );
    await screen.findByText('70,969');
    await userEvent.click(screen.getByRole('button', { name: 'Print' }));
    expect(print).toHaveBeenCalledTimes(1);

    const header = screen.getByTestId('print-header');
    expect(header.classList.contains('print-only')).toBe(true);
    // Bank icon and full name (logo supplied 2026-10-24, design P8).
    expect(within(header).getByRole('img', { name: 'SUCO Bank logo' })).toBeTruthy();
    expect(header.textContent).toContain('SUCO Souharda Sahakari Bank - Payment Reconciliation');
    expect(header.textContent).toContain('Results (all dates)');
    expect(header.textContent).toContain('Module: UPI');
    expect(header.textContent).toContain('Business dates: 01-Jul-2026 to 31-Jul-2026');
    expect(header.textContent).toContain('Order: oldest first');
    expect(header.textContent).toContain('Page 1 of 2 (only this page is printed)');
    expect(header.textContent).toContain('printed by maker1');
    // The filter inputs and buttons are not printed.
    expect(screen.getByRole('region', { name: 'Report filters' }).classList.contains('no-print')).toBe(true);
  });
});
