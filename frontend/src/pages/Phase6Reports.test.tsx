/**
 * Phase 6 slices 6.2-6.4 screens on the built endpoints (contract B3-B8, C2),
 * with the deviations the PM accepted 2026-11-09: volume sides ALL / UNKNOWN,
 * NPCI counts only for Raw Data / Merchant / BBP, missing_dates as a notice;
 * settlement cases apart in the ageing report; no priority anywhere; CSV the
 * only download until "xlsx" is listed; audit rows with no module.
 */
import { notifications } from '@mantine/notifications';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type {
  AgeingReport,
  ApprovalReportRow,
  ExceptionReportRow,
  RoleName,
  UserActivityReportRow,
  VolumeRow,
} from '../api/types';
import { MeProvider } from '../auth/auth';
import { AuditOperations } from '../features/audit/AuditLists';
import { ExportButton } from '../features/reports/ExportButton';
import { ResultsSummaryPanel } from '../features/results/ResultsSummaryPanel';
import { json, makeMe, META, renderWithProviders, routeFetch } from '../test/fixtures';
import { exportBlock, reportBlock } from '../test/reportFixtures';
import { AgeingReportPage, ApprovalReportPage, ExceptionReportPage } from './CaseReportsPages';
import { CaseQueuePage } from './CaseQueuePage';
import { UserActivityReportPage } from './UserActivityReportPage';
import { VolumeReportPage } from './VolumeReportPage';

afterEach(() => {
  // The notifications store is global: no toast leaks into the next test.
  notifications.clean();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const page = <T,>(results: T[], extra: object = {}) => ({
  count: results.length,
  next: null,
  previous: null,
  results,
  ...extra,
});
const callsTo = (fetch: ReturnType<typeof routeFetch>, path: string) =>
  fetch.mock.calls.map((c) => String(c[0])).filter((u) => u.split('?')[0]!.endsWith(path));
const queryOf = (url: string) => new URL(url, 'http://x').searchParams;

const META6 = {
  ...META,
  ageing_buckets: [
    { label: '0-1 d', min_days: 0, max_days: 1 },
    { label: '45+ d', min_days: 46, max_days: null },
  ],
  status_categories: [{ value: 'PENDING_INVESTIGATION', label: 'Pending investigation' }],
  file_types: [
    { code: 'CBS_GL_EXTRACT', register_type: 'CBS_GL_EXTRACT', is_cycle_wise: false, cycles: [] },
    { code: 'RAW_DATA', register_type: 'RAW_DATA', is_cycle_wise: true, cycles: ['1C'] },
    { code: 'DRC', register_type: 'DRC', is_cycle_wise: false, cycles: [] },
    { code: 'NTSL', register_type: 'NTSL', is_cycle_wise: true, cycles: [] },
  ],
};

function renderAt(pattern: string, route: string, element: ReactElement, roles: RoleName[] = ['Auditor']) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path={pattern} element={element} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

// ---------------------------------------------------------------------------
// B3 volume
// ---------------------------------------------------------------------------
const VOL = '/api/v1/upi/reports/volume/';
const vol = (over: Partial<VolumeRow>): VolumeRow => ({
  period: '2026-11-04',
  file_type: 'RAW_DATA',
  file_type_label: 'Raw Data',
  cycle: null,
  sides: [
    { side: 'ACQUIRER', side_label: 'Acquirer', count: 21718, amount: '100.00' },
    { side: 'ISSUER', side_label: 'Issuer', count: 29470, amount: '200.00' },
  ],
  total_count: 51188,
  total_amount: '300.00',
  npci: { approved: 46930, declined: 4258 },
  ...over,
});
const CBS = vol({
  file_type: 'CBS_GL_EXTRACT',
  file_type_label: 'CBS GL Extract',
  sides: [
    { side: 'DEBIT', side_label: 'Debit', count: 27586, amount: '1.00' },
    { side: 'CREDIT', side_label: 'Credit', count: 49451, amount: '2.00' },
    { side: 'UNKNOWN', side_label: 'Direction unknown', count: 1, amount: '5829146.98' },
  ],
  total_count: 77038,
  total_amount: '5829149.98',
  npci: null,
});
const DRC = vol({
  file_type: 'DRC',
  file_type_label: 'Debit Reversal Confirmation Report',
  sides: [{ side: 'ALL', side_label: 'All rows', count: 25, amount: '9.00' }],
  total_count: 25,
  total_amount: '9.00',
  npci: null,
});
const volPage = (rows: VolumeRow[], missing: string[] = []) =>
  page(rows, {
    totals: rows.map(({ period: _p, cycle: _c, ...f }) => f),
    missing_dates: missing,
    report: reportBlock('volume'),
    export: exportBlock([['volume', 'Volume', rows.length * 2]]),
  });

describe('Transaction volume (B3)', () => {
  it('sides per file type, a dash where NPCI counts do not apply, missing dates as a notice', async () => {
    const fetch = routeFetch({ [VOL]: volPage([CBS, vol({}), DRC], ['2026-11-06']), '/meta/': META6 });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/reports/volume',
      '/upi/reports/volume?date_from=2026-11-01&date_to=2026-11-06',
      <VolumeReportPage />,
    );

    const table = await screen.findByRole('table', { name: 'Volume by period and file type' });
    expect(await within(table).findByText('Direction unknown: 1 / 58,29,146.98')).toBeTruthy();
    expect(within(table).getByText('All rows: 25 / 9.00')).toBeTruthy();
    expect(within(table).getByText('Acquirer: 21,718 / 100.00')).toBeTruthy();
    // NPCI approved / declined: Raw Data has them, CBS and DRC show a dash.
    const cbsRow = within(table).getAllByText('CBS GL Extract')[0]!.closest('tr')!;
    expect(within(cbsRow).getAllByText('-')).toHaveLength(2);
    const rawRow = within(table).getByText('Raw Data').closest('tr')!;
    expect(within(rawRow).getByText('46,930')).toBeTruthy();
    // Missing dates are a notice, not an error.
    expect(screen.getByRole('status').textContent).toContain('06-Nov-2026');
    expect(screen.queryByRole('alert')).toBeNull();
    // Totals per file type for the whole range.
    expect(screen.getByRole('table', { name: 'Volume totals per file type, whole range' })).toBeTruthy();
    const q = queryOf(callsTo(fetch, VOL)[0]!);
    expect(q.get('group_by')).toBeNull();
    expect(q.get('ordering')).toBeNull();
  });

  it('per month allows more than 92 days and sends group_by, by_cycle, ordering and file types', async () => {
    const fetch = routeFetch({ [VOL]: volPage([vol({ period: '2026-07', cycle: '1C' })]), '/meta/': META6 });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/reports/volume',
      '/upi/reports/volume?date_from=2026-01-01&date_to=2026-10-31&group_by=month&by_cycle=true&ordering=period&file_type=RAW_DATA,DRC',
      <VolumeReportPage />,
    );
    const table = await screen.findByRole('table', { name: 'Volume by period and file type' });
    expect(await within(table).findByText('Jul-2026')).toBeTruthy();
    expect(within(table).getByText('1C')).toBeTruthy();
    const q = queryOf(callsTo(fetch, VOL)[0]!);
    expect([q.get('group_by'), q.get('by_cycle'), q.get('ordering'), q.get('file_type')]).toEqual([
      'month',
      'true',
      'period',
      'RAW_DATA,DRC',
    ]);
  });

  it('per day keeps the 92-day rule', async () => {
    const fetch = routeFetch({ [VOL]: volPage([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/reports/volume',
      '/upi/reports/volume?date_from=2026-01-01&date_to=2026-10-31',
      <VolumeReportPage />,
    );
    expect(await screen.findByText(/At most 92 days per report/)).toBeTruthy();
    expect(callsTo(fetch, VOL)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// downloads (contract A, RR3)
// ---------------------------------------------------------------------------
describe('Download buttons', () => {
  it('CSV only while the server lists only "csv"; the file is saved under the server name, per sheet', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    const fetch = routeFetch({
      '/reports/settlement/': (_init: RequestInit | undefined, url: string) =>
        new Response('a,b\r\n', {
          status: 200,
          headers: {
            'Content-Type': 'text/csv',
            'Content-Disposition': `attachment; filename="UPI_settlement_2026-07-01_to_2026-07-31_${queryOf(url).get('sheet')}.csv"`,
          },
        }),
    });
    vi.stubGlobal('fetch', fetch);
    renderWithProviders(
      <ExportButton
        info={exportBlock([
          ['dates', 'Dates', 31],
          ['cycles', 'Cycles', 400],
        ])}
        path="/api/v1/upi/reports/settlement/"
        query={{ date_from: '2026-07-01', date_to: '2026-07-31', status: undefined, page: 2, page_size: 50 }}
        fallbackName="x"
      />,
    );
    expect(screen.queryByRole('button', { name: /Excel/ })).toBeNull();
    await userEvent.click(screen.getByRole('textbox', { name: 'CSV sheet' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Cycles (400 rows)', hidden: true }));
    await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    const q = queryOf(String(fetch.mock.calls[0]![0]));
    expect([q.get('export'), q.get('sheet'), q.get('date_from'), q.get('page'), q.get('page_size')]).toEqual([
      'csv',
      'cycles',
      '2026-07-01',
      null,
      null,
    ]);
    const a = click.mock.contexts[0] as HTMLAnchorElement;
    expect(a.download).toBe('UPI_settlement_2026-07-01_to_2026-07-31_cycles.csv');
  });

  it('Excel appears only when the server lists "xlsx"', () => {
    renderWithProviders(
      <ExportButton
        info={exportBlock([['users', 'Users', 2]], { formats: ['xlsx', 'csv'] })}
        path="/api/v1/reports/user-activity/"
        query={{}}
        fallbackName="x"
      />,
    );
    expect(screen.getByRole('button', { name: 'Download Excel' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeTruthy();
    // One sheet: no sheet picker.
    expect(screen.queryByRole('textbox', { name: 'CSV sheet' })).toBeNull();
  });

  it('over the row limit the button is disabled and says why', () => {
    renderWithProviders(
      <ExportButton
        info={exportBlock([['cases', 'Cases', 120_000]])}
        path="/x/"
        query={{}}
        fallbackName="x"
      />,
    );
    expect((screen.getByRole('button', { name: 'Download CSV' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/More than the 1,00,000 rows a download may hold/)).toBeTruthy();
  });

  it("a refused download shows the server's sentence", async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/x/': () =>
          json(400, { detail: 'Excel download is not available on this server; use CSV.', errors: {} }),
      }),
    );
    renderWithProviders(
      <ExportButton info={exportBlock([['a', 'A', 1]])} path="/x/" query={{}} fallbackName="x" />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }));
    expect(await screen.findByText('Excel download is not available on this server; use CSV.')).toBeTruthy();
  });

  it('B8: the date summary offers the whole results list as CSV', async () => {
    const fetch = routeFetch({
      '/results/summary/': {
        business_date: '2026-07-14',
        rule_evaluation: null,
        rules: [],
        by_status_category: [],
        by_priority: [],
        results_total: 0,
        match_groups: { total: 70969, superseded: 0, on_us: 0, cbs_reversed: 0, by_processing_status: {} },
        cases: { total: 0, active: 0 },
      },
      '/reports/meta/': {
        export_formats: ['csv'],
        max_export_rows: 100000,
        max_range_days: 92,
        max_range_months: 24,
        reports: [],
      },
      '/meta/': META,
    });
    vi.stubGlobal('fetch', fetch);
    renderWithProviders(
      <MeProvider me={makeMe(['Maker'])}>
        <ResultsSummaryPanel module="upi" businessDate="2026-07-14" runId={1} enabled />
      </MeProvider>,
    );
    expect(
      await screen.findByRole('button', { name: 'Download CSV (all results of 14-Jul-2026)' }),
    ).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// B4 exceptions
// ---------------------------------------------------------------------------
const EXC = '/api/v1/upi/reports/exceptions/';
const exc = (over: Partial<ExceptionReportRow> = {}): ExceptionReportRow => ({
  id: 307,
  business_date: '2026-09-25',
  rrn: '004383934640',
  case_type: 'TRANSACTION',
  rule_code: 'R13',
  status_category: 'PENDING_INVESTIGATION',
  amount: '110.00',
  status: 'OPEN',
  due_date: '2026-09-26',
  age_days: 45,
  ageing_bucket: '36-45 d',
  is_overdue: true,
  assigned_to: null,
  proposed_action: {
    action_type: 'DRC_102',
    response_code: 'DRC 102 - Reversed online',
    decision: 'APPROVED',
    reference: 'REF1',
  },
  created_at: '2026-10-10T18:56:20Z',
  closed_at: null,
  last_remark: 'Called the branch',
  link: '/upi/cases/307',
  ...over,
});
const SETTLEMENT_CASE = exc({
  id: 9,
  rrn: '',
  case_type: 'SETTLEMENT',
  rule_code: '',
  amount: '1070.50',
  ageing_bucket: '',
  due_date: null,
  is_overdue: false,
  proposed_action: null,
  last_remark: '',
  link: '/upi/cases/9',
});

describe('Exception report (B4)', () => {
  it('no priority anywhere; settlement case with its amount and no bucket; summary by status and rule', async () => {
    const fetch = routeFetch({
      [EXC]: page([exc(), SETTLEMENT_CASE], {
        summary: {
          by_status: [{ status: 'OPEN', count: 2 }],
          by_rule: [
            { rule_code: 'R13', count: 1, open: 1 },
            { rule_code: '', count: 1, open: 1 },
          ],
        },
        report: reportBlock('exceptions'),
        export: exportBlock([
          ['cases', 'Cases', 2],
          ['summary', 'Summary', 3],
        ]),
      }),
      '/meta/': META6,
    });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/reports/exceptions',
      '/upi/reports/exceptions?date_from=2026-09-01&date_to=2026-09-30&rule_code=R13,R01&status=all',
      <ExceptionReportPage />,
      ['Maker'],
    );
    const table = await screen.findByRole('table', { name: 'Exception cases' });
    expect(await within(table).findByRole('link', { name: '004383934640' })).toBeTruthy();
    expect(
      within(table).getByRole('link', { name: 'Settlement (NTSL) 25-Sep-2026' }).getAttribute('href'),
    ).toBe('/upi/cases/9');
    expect(within(table).getByText('1,070.50')).toBeTruthy();
    expect(within(table).getByText('DRC reply 102 (customer reversed online)')).toBeTruthy();
    expect(within(table).getByText('Overdue')).toBeTruthy();
    expect(screen.queryByText(/priority/i)).toBeNull();
    expect(
      within(screen.getByRole('table', { name: 'Cases by rule' })).getByText('Settlement (no rule)'),
    ).toBeTruthy();
    const q = queryOf(callsTo(fetch, EXC)[0]!);
    expect([q.get('rule_code'), q.get('status'), q.get('priority')]).toEqual(['R13,R01', 'all', null]);
    // The sort offers no priority (the server refuses ordering=priority).
    await userEvent.click(screen.getByRole('textbox', { name: 'Sort' }));
    expect(screen.queryByRole('option', { name: /priority/i })).toBeNull();
  });

  it('closed-by-system badge and filter (owner 2026-11-09)', async () => {
    const closed = exc({
      id: 310,
      rrn: '004383934999',
      rule_code: 'R01',
      status: 'COMPLETED',
      closed_at: '2026-11-09T10:00:00Z',
      closed_by_system: true,
      proposed_action: null,
    });
    const fetch = routeFetch({
      [EXC]: page([exc({ closed_by_system: false }), closed], {
        summary: { by_status: [], by_rule: [] },
        report: reportBlock('exceptions'),
        export: exportBlock([['cases', 'Cases', 2]]),
      }),
      '/meta/': META6,
    });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/reports/exceptions',
      '/upi/reports/exceptions?date_from=2026-09-01&date_to=2026-09-30&status=all',
      <ExceptionReportPage />,
      ['Auditor'],
    );
    const table = await screen.findByRole('table', { name: 'Exception cases' });
    const rowOf = async (rrn: string) => (await within(table).findByRole('link', { name: rrn })).closest('tr')!;
    expect(within(await rowOf('004383934999')).getByText('Closed by system')).toBeTruthy();
    expect(within(await rowOf('004383934640')).queryByText('Closed by system')).toBeNull();
    expect(queryOf(callsTo(fetch, EXC)[0]!).get('closed_by_system')).toBeNull();

    await userEvent.click(screen.getByRole('textbox', { name: 'Closed by system' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Closed by system', hidden: true }));
    await waitFor(() =>
      expect(callsTo(fetch, EXC).map((u) => queryOf(u).get('closed_by_system'))).toContain('true'),
    );
    expect(screen.getByTestId('print-header').textContent).toContain('Closed by system: yes');
  });
});

// ---------------------------------------------------------------------------
// B5 ageing + C2 case-queue links
// ---------------------------------------------------------------------------
const AGE = '/api/v1/upi/reports/ageing/';
const cell = (bucket: string, count: number, rule = 'R01') => ({
  bucket,
  count,
  amount: count ? '57111.00' : '0.00',
  overdue: count,
  link: `/upi/cases?status=&rule_code=${rule}&ageing_bucket=${encodeURIComponent(bucket)}`,
});
const AGEING: AgeingReport = {
  buckets: [
    { label: '0-1 d', min_days: 0, max_days: 1 },
    { label: '45+ d', min_days: 46, max_days: null },
  ],
  by_rule: [
    {
      rule_code: 'R01',
      cells: [cell('0-1 d', 0), cell('45+ d', 8)],
      total: { count: 8, amount: '57111.00', overdue: 8 },
    },
  ],
  totals: {
    cells: [cell('0-1 d', 0, ''), cell('45+ d', 8, '')],
    total: { count: 8, amount: '57111.00', overdue: 8 },
  },
  not_aged: { count: 1, amount: '0.00', link: '/upi/cases?status=&case_type=SETTLEMENT' },
  oldest_case: { id: 1, rrn: '000319360086', age_days: 118, link: '/upi/cases/1' },
  report: reportBlock('ageing'),
  export: exportBlock([['by_rule', 'By rule', 3]]),
};

describe('Ageing report (B5)', () => {
  it('rule x bucket grid with queue links; settlement cases apart; no dates and no priority', async () => {
    const fetch = routeFetch({ [AGE]: AGEING, '/meta/': META6 });
    vi.stubGlobal('fetch', fetch);
    renderAt('/:module/reports/ageing', '/upi/reports/ageing?rule_code=R01', <AgeingReportPage />, [
      'Checker',
    ]);
    const grid = await screen.findByRole('table', { name: 'Open cases by rule and age' });
    expect(within(grid).getByRole('link', { name: '8 cases, R01, 45+ d' }).getAttribute('href')).toBe(
      '/upi/cases?status=&rule_code=R01&ageing_bucket=45%2B%20d',
    );
    const notAged = screen.getByRole('group', { name: 'Open cases with no ageing bucket' });
    expect(within(notAged).getByRole('link', { name: '1 open case(s)' }).getAttribute('href')).toBe(
      '/upi/cases?status=&case_type=SETTLEMENT',
    );
    expect(screen.getByRole('link', { name: '000319360086' }).getAttribute('href')).toBe('/upi/cases/1');
    expect(screen.queryByLabelText(/date from/)).toBeNull();
    expect(screen.queryByText(/priority/i)).toBeNull();
    const q = queryOf(callsTo(fetch, AGE)[0]!);
    expect([q.get('rule_code'), q.get('date_from')]).toEqual(['R01', null]);
    expect(screen.getByTestId('print-header').textContent).toContain(
      'Open cases as of 15-Jul-2026 11:30 IST',
    );
  });

  it('the case queue takes ageing_bucket and a comma list of rules from such a link', async () => {
    const fetch = routeFetch({ '/cases/': page([]), '/meta/': META6 });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/cases',
      '/upi/cases?status=&rule_code=R01,R13&ageing_bucket=45%2B%20d',
      <CaseQueuePage />,
      ['Maker'],
    );
    await waitFor(() => expect(callsTo(fetch, '/upi/cases/').length).toBeGreaterThan(0));
    const q = queryOf(callsTo(fetch, '/upi/cases/')[0]!);
    expect([q.get('ageing_bucket'), q.get('rule_code')]).toEqual(['45+ d', 'R01,R13']);
    // Shown under "More filters" (opened because they are set).
    expect(await screen.findByText('More filters (2 set)')).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Age' })).toHaveProperty('value', '45+ d');
    expect(screen.getByRole('textbox', { name: 'Rule' })).toHaveProperty('value', 'R01 / R13');
  });
});

// ---------------------------------------------------------------------------
// B6 / B6s approvals
// ---------------------------------------------------------------------------
const APP = '/api/v1/upi/reports/approvals/';
const APPS = '/api/v1/upi/reports/approvals/settings/';
const action: ApprovalReportRow = {
  id: 4,
  case_id: 8,
  business_date: '2026-07-14',
  rrn: '041315416045',
  rule_code: 'R06',
  action_type: 'MANUAL_CREDIT_RRC_501',
  action_type_label: 'Manual credit + RRC 501',
  response_code: 'RRC 501 - Manually Credited',
  maker: 'suco',
  submitted_at: '2026-10-28T03:30:21Z',
  checker: 'suco',
  decided_at: '2026-10-29T03:37:18Z',
  decision: 'APPROVED',
  self_approved: true,
  rejection_reason: '',
  marked_done_at: '2026-10-29T03:39:09Z',
  reference: '041315416045',
  hours_to_decision: 24.1,
  link: '/upi/cases/8',
};
const APPROVAL_EXPORT = exportBlock([
  ['actions', 'Case actions', 1],
  ['settings', 'Settings changes', 6],
  ['summary', 'Summary', 2],
]);
const approvalAnswer = page([action], {
  summary: {
    by_checker: [{ checker: 'suco', approved: 2, rejected: 0, self_approved: 2 }],
    pending_now: 2,
    oldest_pending: { id: 3, submitted_at: '2026-10-28T03:29:30Z', link: '/upi/cases/1027' },
  },
  report: reportBlock('approvals'),
  export: APPROVAL_EXPORT,
});
const settingsAnswer = page(
  [
    {
      kind: 'response_code',
      kind_label: 'Response code change',
      item_label: 'Edit an existing code: TCC 102 - Online Credited',
      proposed_by: 'suco',
      proposed_at: '2026-10-23T09:37:45Z',
      decided_by: 'suco',
      decided_at: '2026-10-26T17:58:01Z',
      decision: 'APPROVED',
      self_approved: true,
      remarks: 'Owner decision',
      link: '/upi/settings/response-codes',
    },
  ],
  { report: reportBlock('approvals'), export: { ...APPROVAL_EXPORT, row_count: 6 } },
);

describe('Approval report (B6 / B6s)', () => {
  it('Checker: case actions with the summary; the settings tab asks B6s and defaults the CSV to its sheet', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-11-09T06:00:00Z'));
    const fetch = routeFetch({ [APP]: approvalAnswer, [APPS]: settingsAnswer, '/meta/': META6 });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/reports/approvals',
      '/upi/reports/approvals?decision=WITHDRAWN,APPROVED',
      <ApprovalReportPage />,
      ['Checker'],
    );

    const table = await screen.findByRole('table', { name: 'Case action decisions' });
    expect(await within(table).findByRole('link', { name: '041315416045' })).toBeTruthy();
    expect(within(table).getByText('Self-approved')).toBeTruthy();
    expect(within(table).getByText('24.1')).toBeTruthy();
    expect(screen.getByRole('group', { name: 'Pending now' }).textContent).toContain('2');
    // Event dates: the default range ends today, not yesterday.
    const q = queryOf(callsTo(fetch, APP)[0]!);
    expect([q.get('date_from'), q.get('date_to'), q.get('decision')]).toEqual([
      '2026-11-01',
      '2026-11-09',
      'WITHDRAWN,APPROVED',
    ]);
    expect(screen.getByLabelText('Decision date from')).toBeTruthy();

    await userEvent.click(screen.getByRole('tab', { name: 'Settings changes' }));
    const st = await screen.findByRole('table', { name: 'Settings change decisions' });
    expect(await within(st).findByRole('link', { name: 'Response code change' })).toBeTruthy();
    const sq = queryOf(callsTo(fetch, APPS)[0]!);
    // "Withdrawn" is for case actions only: not sent to the settings list (it would be refused).
    expect(sq.get('decision')).toBe('APPROVED');
    expect(screen.getByRole('textbox', { name: 'CSV sheet' })).toHaveProperty(
      'value',
      'Settings changes (6 rows)',
    );
  });

  it('a system-closed case: badge on its withdrawn action, the checker reads "System" (owner 2026-11-09)', async () => {
    const withdrawn: ApprovalReportRow = {
      ...action,
      id: 5,
      case_id: 9,
      rrn: '041315416046',
      rule_code: 'R01',
      self_approved: false,
      checker: null,
      decision: 'WITHDRAWN',
      decided_at: '2026-11-09T05:00:00Z',
      marked_done_at: null,
      reference: '',
      closed_by_system: true,
      link: '/upi/cases/9',
    };
    // An earlier rejection on the same case: the case flag is true, but no badge there.
    const rejected: ApprovalReportRow = {
      ...withdrawn,
      id: 6,
      rrn: '041315416047',
      decision: 'REJECTED',
      checker: 'chk1',
    };
    vi.stubGlobal(
      'fetch',
      routeFetch({ [APP]: { ...approvalAnswer, ...page([withdrawn, rejected]) }, '/meta/': META6 }),
    );
    renderAt('/:module/reports/approvals', '/upi/reports/approvals', <ApprovalReportPage />, ['Auditor']);
    const table = await screen.findByRole('table', { name: 'Case action decisions' });
    const row = (await within(table).findByRole('link', { name: '041315416046' })).closest('tr')!;
    expect(within(row).getByText('Closed by system')).toBeTruthy();
    expect(within(row).getByText('System')).toBeTruthy();
    const other = within(table).getByRole('link', { name: '041315416047' }).closest('tr')!;
    expect(within(other).queryByText('Closed by system')).toBeNull();
    expect(within(other).getByText('chk1')).toBeTruthy();
  });

  it('Maker: no access (the server answers 403 too)', () => {
    const fetch = routeFetch({});
    vi.stubGlobal('fetch', fetch);
    const me = makeMe(['Maker']);
    me.permissions = { ...me.permissions, 'reports.approvals': false };
    renderWithProviders(
      <MeProvider me={me}>
        <Routes>
          <Route path="/:module/reports/approvals" element={<ApprovalReportPage />} />
        </Routes>
      </MeProvider>,
      { route: '/upi/reports/approvals' },
    );
    expect(screen.getByText('Your role does not include this report.')).toBeTruthy();
    expect(callsTo(fetch, APP)).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// B7 user activity
// ---------------------------------------------------------------------------
const UA = '/api/v1/reports/user-activity/';
const user: UserActivityReportRow = {
  user_id: 10,
  username: 'suco',
  full_name: 'suco',
  roles: ['Administrator', 'Checker'],
  is_active: true,
  logins: 22,
  failed_logins: 1,
  last_login: '2026-11-08T20:50:20Z',
  last_login_ip: null,
  cases: { taken: 53, submitted: 4, approved: 2, rejected: 0, marked_done: 2, remarks: 0 },
  files: { uploaded: 172, refused: 0 },
  cbs_dates_selected: 2,
  runs_started: 4,
  settings: { proposed: 6, decided: 6 },
  report_downloads: 3,
  links: {
    case_events: '/audit/case-events?user=suco&date_from=2026-10-15&date_to=2026-11-09',
    user_activity: '/audit/user-activity?user=suco&date_from=2026-10-15&date_to=2026-11-09',
    operations: '/audit/operations?user=suco&date_from=2026-10-15&date_to=2026-11-09',
  },
};

describe('User activity report (B7)', () => {
  it('Auditor: one row per user, audit links, module filter for the work counts only', async () => {
    const fetch = routeFetch({
      [UA]: page([user], {
        report: reportBlock('user_activity', { module: null }),
        export: exportBlock([['users', 'Users', 1]]),
      }),
    });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/reports/user-activity',
      '/reports/user-activity?date_from=2026-10-15&date_to=2026-11-09&module=upi',
      <UserActivityReportPage />,
    );
    const table = await screen.findByRole('table', { name: 'Activity per user' });
    expect(await within(table).findByText('22')).toBeTruthy();
    expect(within(table).getByText('IP not recorded')).toBeTruthy();
    expect(within(table).getByText(/Uploaded 172, refused 0/)).toBeTruthy();
    expect(within(table).getByRole('link', { name: 'Operations of suco' }).getAttribute('href')).toBe(
      '/audit/operations?user=suco&date_from=2026-10-15&date_to=2026-11-09',
    );
    const q = queryOf(callsTo(fetch, UA)[0]!);
    expect(q.get('module')).toBe('upi');
    expect(screen.getByTestId('print-header').textContent).toContain('Module: UPI (work counts only');
    expect(screen.queryByText(/change it in the header/)).toBeNull();
  });

  it('Checker: no access', () => {
    vi.stubGlobal('fetch', routeFetch({}));
    renderAt('/reports/user-activity', '/reports/user-activity', <UserActivityReportPage />, ['Checker']);
    expect(screen.getByText('Your role does not include this report.')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// audit: an operation with no module (a user activity report download)
// ---------------------------------------------------------------------------
describe('Audit operations with no module', () => {
  it('a REPORT_EXPORTED entry with module and link null is listed without a link', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/audit/meta/': {
          modules: [],
          case_event_types: [],
          settings_kinds: [],
          settings_event_types: [],
          user_activity_types: [],
          operation_types: [{ value: 'REPORT_EXPORTED', label: 'Report downloaded' }],
          orderings: ['-created_at', 'created_at'],
        },
        '/audit/operations/': page([
          {
            id: 300,
            created_at: '2026-11-09T05:00:00Z',
            module: null,
            event_type: 'REPORT_EXPORTED',
            event_type_label: 'Report downloaded',
            username: 'auditor',
            source: 'api',
            business_date: null,
            uploaded_file_id: null,
            run_id: null,
            details: { report: 'user_activity', format: 'csv' },
            link: null,
          },
        ]),
      }),
    );
    renderAt('/audit/operations', '/audit/operations', <AuditOperations />);
    const table = await screen.findByRole('table', { name: 'Operations' });
    expect(await within(table).findByText('Report downloaded')).toBeTruthy();
    expect(within(table).getByText('api')).toBeTruthy();
    expect(within(table).queryByRole('link')).toBeNull();
  });
});
