/**
 * Phase 6: Reports page, "Results (all dates)" (B1) and "Settlement (all
 * dates)" (B2) on the report endpoints, the shared filter bar and the print
 * layout. Slice 6.1 built them on GET /dates/; slice 6.2 moved them.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ReconciliationRow, RoleName, SettlementReportRow } from '../api/types';
import { MeProvider } from '../auth/auth';
import { makeMe, META, renderWithProviders, routeFetch } from '../test/fixtures';
import { exportBlock, reportBlock, reconciliationTotals } from '../test/reportFixtures';
import { ALL_DATES_PAGE_SIZE, ResultsAllDatesPage, SettlementAllDatesPage } from './AllDatesPages';
import { ReportsPage } from './ReportsPage';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Shows where a row click went. */
function Where() {
  const l = useLocation();
  return <p data-testid="where">{`${l.pathname}${l.search}${l.hash}`}</p>;
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

const B1 = '/api/v1/upi/reports/reconciliation/';
const B2 = '/api/v1/upi/reports/settlement/';
const callsTo = (fetch: ReturnType<typeof routeFetch>, path: string) =>
  fetch.mock.calls.map((c) => String(c[0])).filter((u) => u.includes(path));
const queryOf = (url: string) => new URL(url, 'http://x').searchParams;

function recRow(over: Partial<ReconciliationRow> = {}): ReconciliationRow {
  return {
    business_date: '2026-07-14',
    files: { expected: 66, present: 64, missing: 2, received: 64 },
    match_groups: {
      total: 70969,
      by_status: { MATCHED: 70000, UNMATCHED: 900, PENDING_RULE_EVALUATION: 69 },
      on_us: 700,
      cbs_reversed: 120,
    },
    results_by_category: { RECONCILED: 70000, PENDING_INVESTIGATION: 120, EXCEPTION: 0 },
    rule_counts: { R09: 70000, R13: 120 },
    cases: { created: 594, open: 120 },
    settlement: { status: 'UNEXPLAINED', unexplained_amount: '1070.50' },
    rule_evaluation: { id: 3, rule_set: 'v3.2', finished_at: '2026-07-15T05:03:00Z', triggered_by: 'ops1' },
    last_run: { id: 9, status: 'SUCCEEDED', requested_by: 'ops1', finished_at: '2026-07-15T05:04:00Z' },
    links: {
      results: '/upi/dates/2026-07-14/results',
      settlement: '/upi/dates/2026-07-14/settlement',
      date: '/upi/dates/2026-07-14',
    },
    ...over,
  };
}

// Files loaded, nothing run yet: opens the date's overview, not its results.
const NOT_RUN = recRow({
  business_date: '2026-07-16',
  match_groups: { total: 0, by_status: {}, on_us: 0, cbs_reversed: 0 },
  results_by_category: {},
  rule_counts: {},
  cases: { created: 0, open: 0 },
  settlement: null,
  rule_evaluation: null,
  last_run: null,
  links: { results: null, settlement: null, date: '/upi/dates/2026-07-16' },
});

function recPage(rows: ReconciliationRow[], over: object = {}) {
  return {
    count: rows.length,
    next: null,
    previous: null,
    results: rows,
    rule_codes: ['R13', 'R09'],
    totals: reconciliationTotals(rows),
    months: [],
    report: reportBlock('reconciliation', {
      versions: { rule_sets: ['v3.2'], ntsl_mappings: [], tolerances: [] },
    }),
    export: exportBlock([
      ['dates', 'Dates', rows.length + 1],
      ['rules', 'Rules by date', rows.length + 1],
    ]),
    ...over,
  };
}

// ---------------------------------------------------------------------------
describe('Reports page (design 5.2, R2)', () => {
  it.each<[RoleName, string[]]>([
    ['Maker', []],
    ['Checker', ['Approval report']],
    ['Auditor', ['Approval report', 'User activity report']],
  ])('%s: every report the role may open is listed (slices 6.2-6.4 built)', (role, gated) => {
    renderAt('/upi/reports', <ReportsPage />, [role]);
    expect(screen.getAllByRole('listitem')).toHaveLength(6 + gated.length);
    expect(screen.getByRole('link', { name: /Results \(all dates\)/ }).getAttribute('href')).toBe(
      '/upi/results',
    );
    expect(screen.getByRole('link', { name: 'Ageing report' }).getAttribute('href')).toBe(
      '/upi/reports/ageing',
    );
    expect(
      screen.getByRole('link', { name: 'Daily / monthly transaction volume' }).getAttribute('href'),
    ).toBe('/upi/reports/volume');
    for (const title of ['Approval report', 'User activity report']) {
      const link = screen.queryByRole('link', { name: title });
      if (gated.includes(title)) expect(link).not.toBeNull();
      else expect(link).toBeNull();
    }
    if (gated.includes('User activity report')) {
      // Not under a module: logins are global (B7).
      expect(screen.getByRole('link', { name: 'User activity report' }).getAttribute('href')).toBe(
        '/reports/user-activity',
      );
    }
  });

  it("follows the server's capabilities once sent (Maker given approvals)", () => {
    const me = makeMe(['Maker']);
    me.permissions = { ...me.permissions, 'reports.approvals': true, 'reports.user_activity': false };
    renderWithProviders(
      <MeProvider me={me}>
        <Routes>
          <Route path="/:module/reports" element={<ReportsPage />} />
        </Routes>
      </MeProvider>,
      { route: '/upi/reports' },
    );
    expect(screen.getByRole('link', { name: 'Approval report' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'User activity report' })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
describe('Results (all dates) on B1', () => {
  it('asks for the range, 50 dates a page, and shows a row per date with whole-range totals', async () => {
    const fetch = routeFetch({ [B1]: recPage([recRow(), NOT_RUN]), '/meta/': META });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results?date_from=2026-07-01&date_to=2026-07-31', <ResultsAllDatesPage />);

    const table = await screen.findByRole('table', { name: 'Results by business date' });
    // In the row and in the whole-range total.
    expect(await within(table).findAllByText('70,969')).toHaveLength(2);
    const q = queryOf(callsTo(fetch, B1)[0]!);
    expect(q.get('date_from')).toBe('2026-07-01');
    expect(q.get('date_to')).toBe('2026-07-31');
    expect(q.get('page_size')).toBe(String(ALL_DATES_PAGE_SIZE));
    expect(ALL_DATES_PAGE_SIZE).toBe(50);
    expect(q.get('ordering')).toBeNull();

    expect(within(table).getByRole('link', { name: 'Results for 14-Jul-2026' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-14#step-results',
    );
    expect(within(table).getByRole('link', { name: 'Overview for 16-Jul-2026' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-16',
    );
    const foot = within(table)
      .getByRole('rowheader', { name: /Total, whole range \(2 dates\)/ })
      .closest('tr')!;
    expect(within(foot).getByText('594')).toBeTruthy();
    expect(within(foot).getByText(/Unexplained settlement: 1,070.50/)).toBeTruthy();
    expect(screen.getByText('2 business date(s) in the range')).toBeTruthy();
    // "Figures as of" is the server's generated_at; versions are listed.
    expect(
      screen.getByText(/^Figures as of 15-Jul-2026 11:30 IST.*Produced with: Rule set v3\.2/),
    ).toBeTruthy();
    // CSV only (IT has not approved Excel yet).
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Excel/ })).toBeNull();
  });

  it('month subtotals when the range spans months; outcome and rule breakdowns', async () => {
    const rows = [recRow(), recRow({ business_date: '2026-08-01' })];
    const months = [
      { month: '2026-08', ...reconciliationTotals([rows[1]!]) },
      { month: '2026-07', ...reconciliationTotals([rows[0]!]) },
    ];
    vi.stubGlobal('fetch', routeFetch({ [B1]: recPage(rows, { months }), '/meta/': META }));
    renderAt('/upi/results?date_from=2026-07-01&date_to=2026-08-31', <ResultsAllDatesPage />);
    const table = await screen.findByRole('table', { name: 'Results by business date' });
    expect(await within(table).findByRole('rowheader', { name: 'Total 2026-07' })).toBeTruthy();
    expect(within(table).getByRole('rowheader', { name: 'Total 2026-08' })).toBeTruthy();
    const rules = screen.getByRole('table', { name: 'Results by rule, by date', hidden: true });
    expect(
      within(rules)
        .getAllByRole('columnheader', { hidden: true })
        .map((h) => h.textContent),
    ).toEqual(['Business date', 'R13', 'R09']);
    // Only outcomes with a result in the range (EXCEPTION is 0).
    const outcome = screen.getByRole('table', { name: 'Results by outcome, by date', hidden: true });
    expect(within(outcome).getAllByRole('columnheader', { hidden: true })).toHaveLength(3);
  });

  it('"Only dates with open cases" goes to the server and the address', async () => {
    const fetch = routeFetch({ [B1]: recPage([recRow()]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results?date_from=2026-07-01&date_to=2026-07-31', <ResultsAllDatesPage />);
    await screen.findByRole('link', { name: 'Results for 14-Jul-2026' });
    await userEvent.click(screen.getByLabelText('Only dates with open cases'));
    await waitFor(() => expect(queryOf(callsTo(fetch, B1).at(-1)!).get('open_cases_only')).toBe('true'));
  });

  it('a click anywhere on a row opens the per-date results', async () => {
    vi.stubGlobal('fetch', routeFetch({ [B1]: recPage([recRow()]) }));
    renderAt('/upi/results?date_from=2026-07-01&date_to=2026-07-31', <ResultsAllDatesPage />);
    const table = await screen.findByRole('table', { name: 'Results by business date' });
    await userEvent.click((await within(table).findAllByText('70,969'))[0]!);
    expect(screen.getByTestId('where').textContent).toBe('/upi/dates/2026-07-14#step-results');
  });

  it('the month of the latest business date, up to yesterday, when the address has no dates', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z'));
    const fetch = routeFetch({ [B1]: recPage([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results', <ResultsAllDatesPage />);
    expect(await screen.findByText(/No business dates in this range/)).toBeTruthy();
    const q = queryOf(callsTo(fetch, B1)[0]!);
    // 23 Oct: ops are reconciling 22 Oct, so the range ends there, not today.
    expect([q.get('date_from'), q.get('date_to')]).toEqual(['2026-10-01', '2026-10-22']);
  });

  it('on 1 Nov the default and "This month" are October', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-01T06:00:00Z'));
    const fetch = routeFetch({ [B1]: recPage([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results?date_from=2026-07-01&date_to=2026-07-31', <ResultsAllDatesPage />);
    await screen.findByText(/No business dates in this range/);
    await userEvent.click(screen.getByRole('button', { name: 'This month' }));
    await waitFor(() => {
      const q = queryOf(callsTo(fetch, B1).at(-1)!);
      expect([q.get('date_from'), q.get('date_to')]).toEqual(['2026-10-01', '2026-10-31']);
    });
  });

  it('the range pickers stop at the latest business date', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z'));
    vi.stubGlobal('fetch', routeFetch({ [B1]: recPage([]) }));
    renderAt('/upi/results', <ResultsAllDatesPage />);
    await screen.findByText(/No business dates in this range/);
    await userEvent.click(screen.getByLabelText('Business date to'));
    const day = (label: string) =>
      screen.getByRole('button', { name: label, hidden: true }) as HTMLButtonElement;
    await screen.findByRole('button', { name: '22 October 2026', hidden: true });
    expect(day('22 October 2026').disabled).toBe(false);
    expect(day('23 October 2026').disabled).toBe(true);
  });

  it('"Last 7 days" is the 7 business dates ending yesterday; "Last month" resets the page', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z'));
    const fetch = routeFetch({ [B1]: recPage([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results?page=2', <ResultsAllDatesPage />);
    await screen.findByText(/No business dates in this range/);
    await userEvent.click(screen.getByRole('button', { name: 'Last 7 days' }));
    await waitFor(() => {
      const q = queryOf(callsTo(fetch, B1).at(-1)!);
      expect([q.get('date_from'), q.get('date_to'), q.get('page')]).toEqual([
        '2026-10-16',
        '2026-10-22',
        '1',
      ]);
    });
    await userEvent.click(screen.getByRole('button', { name: 'Last month' }));
    await waitFor(() => {
      const q = queryOf(callsTo(fetch, B1).at(-1)!);
      expect([q.get('date_from'), q.get('date_to')]).toEqual(['2026-09-01', '2026-09-30']);
    });
  });

  it('an over-long range in the address loads nothing and says why', async () => {
    const fetch = routeFetch({ [B1]: recPage([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt('/upi/results?date_from=2026-01-01&date_to=2026-07-31', <ResultsAllDatesPage />);
    expect(await screen.findByText(/At most 92 days per report/)).toBeTruthy();
    expect(callsTo(fetch, B1)).toHaveLength(0);
  });

  it('order and page come from the address', async () => {
    const fetch = routeFetch({ [B1]: recPage([recRow()], { count: 60 }) });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/upi/results?date_from=2026-07-01&date_to=2026-08-31&ordering=business_date&page=2',
      <ResultsAllDatesPage />,
    );
    await screen.findByRole('link', { name: 'Results for 14-Jul-2026' });
    const q = queryOf(callsTo(fetch, B1)[0]!);
    expect([q.get('ordering'), q.get('page')]).toEqual(['business_date', '2']);
    expect(screen.getByRole('button', { name: 'Dates page 1' })).toBeTruthy();
  });

  it("a server refusal is shown in the server's words", async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        [B1]: () =>
          new Response(
            JSON.stringify({
              detail: 'Invalid input',
              errors: { ordering: ['Allowed: -business_date, business_date'] },
            }),
            { status: 400, headers: { 'Content-Type': 'application/json' } },
          ),
      }),
    );
    renderAt('/upi/results?date_from=2026-07-01&date_to=2026-07-31', <ResultsAllDatesPage />);
    expect(await screen.findByText(/Invalid input Allowed: -business_date, business_date/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
function setRow(over: Partial<SettlementReportRow> = {}): SettlementReportRow {
  return {
    business_date: '2026-07-14',
    status: 'UNEXPLAINED',
    status_label: 'Unexplained difference',
    reasons: ['Cycle 3C: NTSL net differs from CBS net'],
    computed_at: '2026-07-15T05:03:30Z',
    self_check: { cycles_pass: 9, cycles_total: 10 },
    sop: {
      cbs_net: '1000.00',
      ntsl_net: '2070.50',
      difference: '-1070.50',
      explained: '0.00',
      unexplained: '-1070.50',
    },
    unexplained_amount: '1070.50',
    unexplained_count: 3,
    fees_net: '-35.00',
    tolerance_version: 'v1',
    ntsl_mapping_version: 'v2',
    settlement_case: { id: 77, status: 'OPEN', link: '/upi/cases/77' },
    links: { settlement: '/upi/dates/2026-07-14/settlement', date: '/upi/dates/2026-07-14' },
    ...over,
  };
}
const NO_RESULT = setRow({
  business_date: '2026-07-16',
  status: null,
  status_label: null,
  reasons: [],
  computed_at: null,
  self_check: { cycles_pass: 0, cycles_total: 0 },
  sop: null,
  unexplained_amount: null,
  unexplained_count: null,
  fees_net: null,
  tolerance_version: null,
  ntsl_mapping_version: null,
  settlement_case: null,
  links: { settlement: null, date: '/upi/dates/2026-07-16' },
});

function setPage(rows: SettlementReportRow[]) {
  return {
    count: rows.length,
    next: null,
    previous: null,
    results: rows,
    totals: {
      by_status: { TALLIED: 3, UNEXPLAINED: 1, NOT_READY: 0, NONE: 1 },
      unexplained_amount: '1070.50',
      fees_net: '-35.00',
    },
    report: reportBlock('settlement'),
    export: exportBlock([
      ['dates', 'Dates', rows.length + 1],
      ['cycles', 'Cycles', 66],
    ]),
  };
}

describe('Settlement (all dates) on B2', () => {
  it('status, self-check, SOP figures, case link and a summary of the whole range', async () => {
    vi.stubGlobal('fetch', routeFetch({ [B2]: setPage([setRow(), NO_RESULT]), '/meta/': META }));
    renderAt('/upi/settlement?date_from=2026-07-01&date_to=2026-07-31', <SettlementAllDatesPage />, [
      'Auditor',
    ]);
    const table = await screen.findByRole('table', { name: 'Settlement by business date' });
    expect(await within(table).findByText('Cycle 3C: NTSL net differs from CBS net')).toBeTruthy();
    expect(within(table).getByText('9 of 10 cycles pass')).toBeTruthy();
    expect(within(table).getByText('-1,070.50')).toBeTruthy();
    expect(within(table).getByRole('link', { name: 'Settlement case 77' }).getAttribute('href')).toBe(
      '/upi/cases/77',
    );
    expect(within(table).getByRole('link', { name: 'Overview for 16-Jul-2026' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-16',
    );
    expect(within(table).getByText('not computed')).toBeTruthy();
    expect(within(table).getByText('Mapping v2, Tolerance v1')).toBeTruthy();

    const summary = screen.getByRole('group', { name: 'Settlement summary, whole range' });
    expect(summary.textContent).toContain('Tallied: 3');
    expect(summary.textContent).toContain('Unexplained: 1');
    expect(summary.textContent).toContain('Not computed: 1');
    expect(summary.textContent).toContain('Unexplained, whole range: 1,070.50');
    expect(summary.textContent).toContain('Fees net, whole range: -35.00');
    expect(summary.textContent).not.toContain('Not ready');
    // Two sheets: the CSV asks which one.
    expect(screen.getByRole('textbox', { name: 'CSV sheet' })).toBeTruthy();
  });

  it('the status filter is a comma list in the request', async () => {
    const fetch = routeFetch({ [B2]: setPage([setRow()]) });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/upi/settlement?date_from=2026-07-01&date_to=2026-07-31&status=UNEXPLAINED,NONE',
      <SettlementAllDatesPage />,
    );
    await screen.findByRole('table', { name: 'Settlement by business date' });
    expect(queryOf(callsTo(fetch, B2)[0]!).get('status')).toBe('UNEXPLAINED,NONE');
    expect(screen.getByTestId('print-header').textContent).toContain('Status: Unexplained, Not computed');
  });

  it('a row click opens the per-date settlement', async () => {
    vi.stubGlobal('fetch', routeFetch({ [B2]: setPage([setRow()]) }));
    renderAt('/upi/settlement?date_from=2026-07-01&date_to=2026-07-31', <SettlementAllDatesPage />);
    await userEvent.click(await screen.findByText('9 of 10 cycles pass'));
    expect(screen.getByTestId('where').textContent).toBe('/upi/dates/2026-07-14/settlement');
  });
});

// ---------------------------------------------------------------------------
describe('print layout', () => {
  it('Print opens the browser print; the print header carries the filters, page, versions and user', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => {});
    vi.stubGlobal('fetch', routeFetch({ [B1]: recPage([recRow()], { count: 60 }) }));
    renderAt(
      '/upi/results?date_from=2026-07-01&date_to=2026-07-31&ordering=business_date',
      <ResultsAllDatesPage />,
    );
    await screen.findByRole('link', { name: 'Results for 14-Jul-2026' });
    await userEvent.click(screen.getByRole('button', { name: 'Print' }));
    expect(print).toHaveBeenCalledTimes(1);

    const header = screen.getByTestId('print-header');
    expect(header.classList.contains('print-only')).toBe(true);
    expect(within(header).getByRole('img', { name: 'SUCO Bank logo' })).toBeTruthy();
    expect(header.textContent).toContain('SUCO Souharda Sahakari Bank - Payment Reconciliation');
    expect(header.textContent).toContain('Results (all dates)');
    expect(header.textContent).toContain('Module: UPI');
    expect(header.textContent).toContain('Business dates: 01-Jul-2026 to 31-Jul-2026');
    expect(header.textContent).toContain('Order: oldest first');
    expect(header.textContent).toContain('Page 1 of 2 (only this page is printed)');
    expect(header.textContent).toContain('Produced with: Rule set v3.2');
    expect(header.textContent).toContain('printed by maker1');
    expect(screen.getByRole('region', { name: 'Report filters' }).classList.contains('no-print')).toBe(true);
  });
});
