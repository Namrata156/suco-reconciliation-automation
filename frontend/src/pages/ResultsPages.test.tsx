import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type {
  GroupDetail,
  ResultRow,
  ResultSummary,
  SelfCheck,
  SelfCheckRow,
  SettlementDate,
  SettlementTransactionsPage,
} from '../api/types';
import { MeProvider } from '../auth/auth';
import { SettlementTransactions } from '../features/settlement/SettlementTransactions';
import { selfCheckLine } from '../features/settlement/SelfCheckTable';
import { ResultsSummaryPanel } from '../features/results/ResultsSummaryPanel';
import { isActive } from '../layout/AppLayout';
import { json, makeMe, META, renderWithProviders, routeFetch } from '../test/fixtures';
import { GroupDetailPage } from './GroupDetailPage';
import { resultOrderings, ResultsPage } from './ResultsPage';
import { SettlementPage } from './SettlementPage';

afterEach(() => {
  vi.unstubAllGlobals();
});

const page = <T,>(results: T[], extra: object = {}) => ({
  count: results.length,
  next: null,
  previous: null,
  results,
  ...extra,
});

function renderAt(path: string, route: string, element: ReactElement) {
  return renderWithProviders(
    <MeProvider me={makeMe(['Auditor'])}>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

const leg = (amount: string | null, o: Partial<ResultRow['cbs']> = {}) => ({
  present: amount !== null,
  amount,
  direction: amount ? 'CREDIT' : null,
  response_code: null,
  ...o,
});

function resultRow(o: Partial<ResultRow> = {}): ResultRow {
  return {
    id: 41,
    rrn: '419512345678',
    business_date: '2026-07-14',
    settlement_cycle: '1C',
    processing_status: 'MATCHED',
    cbs: leg('1000.00'),
    npci: leg('1000.00', { response_code: '00' }),
    overlays: [],
    amount_match: true,
    direction_match: true,
    is_on_us: false,
    cbs_reversed: false,
    is_superseded: false,
    rule_result: {
      id: 9,
      rule_code: 'R01',
      status_category: 'RECONCILED',
      priority: 'LOW',
      due_date: null,
      due_cutoff_time: null,
    },
    case: null,
    ...o,
  };
}

const SUMMARY: ResultSummary = {
  business_date: '2026-07-14',
  rule_evaluation: {
    id: 5,
    rule_set: 'v3.2',
    started_at: '2026-07-15T05:00:00Z',
    finished_at: '2026-07-15T05:01:00Z',
    group_count: 69000,
    superseded_count: 0,
    calendar_incomplete_count: 0,
    triggered_by: 'ops1',
  },
  rules: [
    {
      rule_code: 'R01',
      name: 'Debited and reversed',
      count: 120,
      status_category: 'REVERSAL_REQUIRED',
      priority: 'LOW',
    },
    {
      rule_code: 'R13',
      name: 'Investigate',
      count: 0,
      status_category: 'PENDING_INVESTIGATION',
      priority: 'MEDIUM',
    },
  ],
  by_status_category: [{ value: 'REVERSAL_REQUIRED', label: 'Reversal required', count: 120 }],
  by_priority: [{ value: 'LOW', label: 'Low', count: 120 }],
  results_total: 120,
  match_groups: {
    total: 120,
    superseded: 3,
    on_us: 2,
    cbs_reversed: 1,
    by_processing_status: { MATCHED: 120 },
  },
  cases: { total: 4, active: 4 },
};

describe('Results summary on the date overview (design 4.12)', () => {
  it('links each rule count into the filtered results list', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/results/summary/': SUMMARY, '/meta/': META }));
    renderWithProviders(<ResultsSummaryPanel module="upi" businessDate="2026-07-14" runId={5} enabled />);
    const r01 = await screen.findByRole('link', { name: 'R01: 120 results' });
    expect(r01.getAttribute('href')).toBe('/upi/dates/2026-07-14/results?rule_code=R01');
    expect(screen.getByRole('link', { name: 'Open the results list' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-14/results',
    );
    expect(screen.queryByRole('link', { name: /R13/ })).toBeNull(); // 0: no link
  });

  it('asks nothing while the date has no matrix and no rule run', () => {
    const fetch = routeFetch({});
    vi.stubGlobal('fetch', fetch);
    renderWithProviders(
      <ResultsSummaryPanel module="upi" businessDate="2026-07-14" runId={null} enabled={false} />,
    );
    expect(screen.getByText(/No reconciliation results for this date yet/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('Results list (design 4.12, LARGE)', () => {
  it('sends the address filters, one page of 50, and links each group and case', async () => {
    const fetch = routeFetch({
      '/dates/2026-07-14/results/': page([
        resultRow(),
        resultRow({
          id: 42,
          rrn: '419500000002',
          case: { id: 77, status: 'OPEN', priority: 'LOW', rule_code: 'R01', assigned_to: null },
        }),
      ]),
      '/meta/': META,
    });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/dates/:businessDate/results',
      '/upi/dates/2026-07-14/results?rule_code=R01&presence=CBS_ONLY',
      <ResultsPage />,
    );

    const table = await screen.findByRole('table', { name: 'Reconciliation results' });
    await within(table).findByRole('link', { name: '419512345678' });
    const call = fetch.mock.calls.map((c) => String(c[0])).find((u) => u.includes('/results/'));
    expect(call).toContain('rule_code=R01');
    expect(call).toContain('presence=CBS_ONLY');
    expect(call).toContain('page_size=50');
    expect(within(table).getByRole('link', { name: '419512345678' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-14/groups/41',
    );
    expect(within(table).getByRole('link', { name: 'Case for 419500000002' }).getAttribute('href')).toBe(
      '/upi/cases/77',
    );
    expect(screen.getByText('Showing 1-2 of 2 match groups')).toBeTruthy();
  });

  it('says when the date has no results (404)', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/results/': () => json(404, { detail: 'No reconciliation results for this date.' }),
        '/meta/': META,
      }),
    );
    renderAt('/:module/dates/:businessDate/results', '/upi/dates/2026-07-15/results', <ResultsPage />);
    expect(await screen.findByText('No reconciliation results for 15-Jul-2026 yet.')).toBeTruthy();
  });

  it('offers each meta ordering both ways', () => {
    const o = resultOrderings(['priority', 'rrn', 'rule_code']);
    expect(o.map((x) => x.value)).toEqual(['', 'priority', '-priority', '-rrn', 'rule_code', '-rule_code']);
    expect(o.find((x) => x.value === 'priority')?.label).toBe('Priority (HIGH first)');
  });
});

describe('Match group detail (design 4.13)', () => {
  const group: GroupDetail = {
    id: 41,
    rrn: '419512345678',
    business_date: '2026-07-14',
    settlement_cycle: '1C',
    processing_status: 'MATCHED',
    flags: {
      amount_match: false,
      direction_match: true,
      status_match: true,
      cbs_reversed: false,
      is_on_us: false,
      is_superseded: false,
    },
    superseded_at: null,
    legs: [
      {
        leg: 'CBS',
        kind: 'presence',
        present: true,
        amount: '1000.00',
        direction: 'CREDIT',
        response_code: null,
        ssk_side: '',
        dispute_type: '',
        original_not_loaded: null,
        fact: {
          id: 3,
          register_type: 'CBS_GL_EXTRACT',
          business_date: '2026-07-14',
          amount: '1000.00',
          txn_amount: null,
          direction: 'CREDIT',
          settlement_cycle: null,
          uploaded_file_id: 12,
          filename: 'cbs_14072026.xls',
          row_reference: '881',
        },
        paired_fact: null,
      },
    ],
    overlay_rows: [],
    rule_result: {
      id: 9,
      rule_code: 'R01',
      rule_name: 'Debited and reversed',
      rule_set: 'v3.2',
      run_id: 5,
      status_category: 'REVERSAL_REQUIRED',
      priority: 'LOW',
      action: 'DRC 102',
      response_code: 'DRC 102 - Reversed online',
      reason: 'CBS debit reversed',
      tat_start_date: '2026-07-14',
      tat_label: 'T+1',
      tat_unresolved: false,
      due_date: '2026-07-15',
      due_cutoff_time: null,
      calendar_incomplete: false,
      rbi_compensation: null,
      facts: { cbs_amount: '1000.00' },
      evaluated_at: '2026-07-15T05:01:00Z',
      is_superseded: false,
      superseded_at: null,
    },
    earlier_rule_results: [],
    case: { id: 77, status: 'OPEN', priority: 'LOW', rule_code: 'R01', assigned_to: 'maker1' },
    created_at: '2026-07-15T05:00:00Z',
    updated_at: '2026-07-15T05:00:00Z',
  };

  it('shows the legs with their source file, the flags, the rule result and the case', async () => {
    const earlier = {
      ...group.rule_result!,
      id: 8,
      rule_code: 'R13',
      is_superseded: true,
      superseded_at: '2026-07-15T05:01:00Z',
    };
    vi.stubGlobal(
      'fetch',
      routeFetch({ '/groups/41/': { ...group, earlier_rule_results: [earlier] }, '/meta/': META }),
    );
    renderAt(
      '/:module/dates/:businessDate/groups/:groupId',
      '/upi/dates/2026-07-14/groups/41',
      <GroupDetailPage />,
    );

    expect(await screen.findByRole('heading', { name: 'Match group 419512345678' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'cbs_14072026.xls' }).getAttribute('href')).toBe('/upi/files/12');
    expect(screen.getByText('Amount match: No')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open case #77' }).getAttribute('href')).toBe('/upi/cases/77');
    expect(within(screen.getByRole('table', { name: 'Earlier rule results' })).getByText('R13')).toBeTruthy();
    expect(screen.getAllByText('CBS debit reversed')).toHaveLength(2); // current + earlier
  });
});

// ---------------------------------------------------------------------------
// settlement (4.14 / 4.15)
// ---------------------------------------------------------------------------
const fig = { ntsl: '10.00', computed: '10.00', difference: '0.00' };
const checkRow = (cycle: string, status = 'PASS'): SelfCheckRow => ({
  id: cycle.length * 100 + cycle.charCodeAt(0),
  cycle,
  status,
  failures: null,
  map_version: 1,
  line_count: 40,
  unmapped_count: 0,
  unmapped_lines: [],
  category_totals: {},
  subtotal_debit: fig,
  subtotal_credit: fig,
  settlement_net: fig,
  final_net: fig,
  checked_at: '2026-07-15T05:02:00Z',
  source: 'run',
});
const CYCLES = ['1C', '2C', '3C', '4C', '5C', '6C', '7C', '8C', '9C', '10C', 'DC1', 'DC2'];
const ALL_PASS: SelfCheck = {
  cycles: 12,
  pass: 12,
  fail: 0,
  missing: 0,
  all_pass: true,
  results: CYCLES.map((c) => checkRow(c)),
};

const SETTLEMENT: SettlementDate = {
  business_date: '2026-07-14',
  result: {
    result_id: 3,
    result_is_current: true,
    status: 'TALLIED',
    computed_at: '2026-07-15T05:03:00Z',
    reasons: [],
    tolerance: { amount: '0.00', txn_count: 0 },
    sop: {
      cbs_net: '5000.00',
      ntsl_net: '5000.00',
      difference: '0.00',
      explained: '0.00',
      unexplained: '0.00',
    },
    fees_net: '-12.40',
    cycles: [],
    map_version: 1,
    tolerance_version: 1,
    rule_run_id: 5,
    source: 'run',
    unexplained_amount: '0.00',
    unexplained_count: 0,
    transaction_counts: { npci: 2, sop: 1, adjustments: 0, z: 0 },
  },
  self_check: ALL_PASS,
  case: null,
  earlier_results: [],
};

const txPage = (
  kind: SettlementTransactionsPage['kind'],
  fields: string[],
  rows: Record<string, unknown>[],
) => ({
  ...page(rows),
  kind,
  fields,
  result_id: 3,
  result_status: 'TALLIED',
  counts: { npci: 2, sop: 1, adjustments: 0, z: 0 },
});

describe('Settlement per date (design 4.14)', () => {
  it('shows 12/12 cycles PASS, unexplained Rs 0.00, no case, and the transactions by kind', async () => {
    const fetch = routeFetch({
      '/dates/2026-07-14/settlement/': SETTLEMENT,
      '/settlement/transactions/': (_init: RequestInit | undefined, url: string) =>
        url.includes('kind=sop')
          ? json(
              200,
              txPage(
                'sop',
                ['rrn', 'rule', 'g_rule', 'amount'],
                [{ rrn: '4195000001', rule: 'R12', g_rule: 'G07', amount: '5.00' }],
              ),
            )
          : json(
              200,
              txPage(
                'npci',
                ['rrn', 'g_rule', 'amount'],
                [{ rrn: '4195000009', g_rule: 'G05', amount: '-1070.50' }],
              ),
            ),
      '/meta/': META,
    });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/dates/:businessDate/settlement',
      '/upi/dates/2026-07-14/settlement',
      <SettlementPage />,
    );

    expect(await screen.findByText('Self-check: 12 of 12 cycles PASS')).toBeTruthy();
    expect(screen.getByText('Unexplained: Rs 0.00 (0 transactions)')).toBeTruthy();
    expect(screen.getByText('No settlement case for this date (tallied).')).toBeTruthy();
    expect(
      within(screen.getByRole('table', { name: 'NTSL self-check per cycle' })).getAllByText('PASS'),
    ).toHaveLength(12);

    const tx = screen.getByRole('table', { name: 'Contributing transactions' });
    expect(await within(tx).findByText('-1,070.50')).toBeTruthy();
    await userEvent.click(screen.getByRole('radio', { name: /SOP check items/ }));
    expect(await within(tx).findByText('G07')).toBeTruthy();
    const sopCall = fetch.mock.calls.map((c) => String(c[0])).find((u) => u.includes('kind=sop'));
    expect(sopCall).toMatch(/^\/api\/v1\/upi\/dates\/2026-07-14\/settlement\/transactions\/\?kind=sop/);
  });

  it('links the settlement case of an UNEXPLAINED date', async () => {
    const unexplained = {
      ...SETTLEMENT,
      result: {
        ...SETTLEMENT.result!,
        status: 'UNEXPLAINED',
        reasons: ['Cycle 1C remitter differs by Rs 750.00'],
      },
      case: { id: 90, status: 'OPEN', priority: 'HIGH', needs_review: false, assigned_to: null },
    };
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/settlement/': unexplained,
        '/settlement/transactions/': txPage('npci', [], []),
        '/meta/': META,
      }),
    );
    renderAt(
      '/:module/dates/:businessDate/settlement',
      '/upi/dates/2026-07-14/settlement',
      <SettlementPage />,
    );
    expect((await screen.findByRole('link', { name: 'Open settlement case #90' })).getAttribute('href')).toBe(
      '/upi/cases/90',
    );
    expect(screen.getByText('Cycle 1C remitter differs by Rs 750.00')).toBeTruthy();
  });

  it('counts FAIL and MISSING cycles in the self-check line', () => {
    expect(selfCheckLine({ ...ALL_PASS, pass: 10, fail: 1, missing: 1 })).toBe(
      '10 of 12 cycles PASS (1 FAIL, 1 MISSING)',
    );
    expect(selfCheckLine({ cycles: 0, pass: 0, fail: 0, missing: 0, all_pass: false, results: [] })).toBe(
      'No NTSL self-check for this date yet',
    );
  });

  it('reads a settlement case’s transactions from the case address (design 4.15)', async () => {
    const fetch = routeFetch({
      '/cases/90/settlement/transactions/': txPage('npci', ['rrn'], [{ rrn: '4195000009' }]),
    });
    vi.stubGlobal('fetch', fetch);
    renderWithProviders(<SettlementTransactions module="upi" source={{ caseId: 90 }} meta={META} />);
    expect(await screen.findByText('4195000009')).toBeTruthy();
    await userEvent.type(screen.getByRole('textbox', { name: 'Filter transactions by RRN' }), '4195000009');
    await userEvent.click(screen.getByRole('button', { name: 'Apply transaction filters' }));
    await waitFor(() =>
      expect(fetch.mock.calls.map((c) => String(c[0]))).toContain(
        '/api/v1/upi/cases/90/settlement/transactions/?kind=npci&page=1&page_size=50&rrn=4195000009',
      ),
    );
  });
});

describe('menu highlighting', () => {
  it('keeps Settings highlighted on its tabs only when asked to', () => {
    expect(isActive('/upi/settings', '/upi/settings/tolerance', '', true)).toBe(true);
    expect(isActive('/upi/settings', '/upi/settings/tolerance', '')).toBe(false);
    expect(isActive('/upi', '/upi/settings', '', false)).toBe(false);
  });
});
