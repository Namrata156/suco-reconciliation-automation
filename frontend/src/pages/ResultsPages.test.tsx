import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type {
  GroupDetail,
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
import { MENU } from '../lib/roles';
import { json, makeMe, META, renderWithProviders, routeFetch } from '../test/fixtures';
import { GroupDetailPage } from './GroupDetailPage';
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
  it('counts per rule, plain numbers: the results list is gone (U3 4.5)', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/results/summary/': SUMMARY, '/meta/': META }));
    renderWithProviders(
      <MeProvider me={makeMe(['Auditor'])}>
        <ResultsSummaryPanel module="upi" businessDate="2026-07-14" runId={5} enabled />
      </MeProvider>,
    );
    const table = await screen.findByRole('table', { name: 'Results per rule for 14-Jul-2026' });
    expect(screen.queryByRole('link')).toBeNull();
    // Only the rules that occurred, plain name first (U1 3.5); no 0-count row.
    expect(within(table).getByText('Debited and reversed')).toBeTruthy();
    expect(within(table).queryByText('Investigate')).toBeNull();
    expect(within(table).queryByText('R13')).toBeNull();
    // Ops' words (4.4).
    expect(screen.getByText('Transactions')).toBeTruthy();
    expect(screen.getByText('3 replaced by a later run (not counted)')).toBeTruthy();
    expect(screen.getByText('By outcome')).toBeTruthy();
    expect(screen.queryByText(/match group|superseded|status category/i)).toBeNull();
    // The case priority is not shown (the summary still carries it).
    expect(screen.queryByText('By priority')).toBeNull();
    expect(within(table).queryByRole('columnheader', { name: 'Priority' })).toBeNull();
  });

  it('asks nothing while the date has no matrix and no rule run', () => {
    const fetch = routeFetch({});
    vi.stubGlobal('fetch', fetch);
    renderWithProviders(
      <MeProvider me={makeMe(['Auditor'])}>
        <ResultsSummaryPanel module="upi" businessDate="2026-07-14" runId={null} enabled={false} />
      </MeProvider>,
    );
    expect(screen.getByText(/No reconciliation results for this date yet/)).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('Transaction (match group) detail (design 4.13)', () => {
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

    expect(await screen.findByRole('heading', { name: 'Transaction 419512345678' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'cbs_14072026.xls' }).getAttribute('href')).toBe('/upi/files/12');
    expect(screen.getByText('Amount match: No')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open case #77' }).getAttribute('href')).toBe('/upi/cases/77');
    expect(within(screen.getByRole('table', { name: 'Earlier rule results' })).getByText('R13')).toBeTruthy();
    expect(screen.getAllByText('CBS debit reversed')).toHaveLength(2); // current + earlier
    // No case priority, neither on the current result nor on the earlier ones.
    expect(screen.queryByText('Priority')).toBeNull();
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
  gst_warning: false,
  gst_check: null,
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
  gst_warnings: 0,
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
        url.includes('kind=unexplained')
          ? json(
              200,
              txPage(
                'unexplained',
                ['source', 'source_label', 'rrn', 'amount', 'detail'],
                [
                  {
                    source: 'NPCI_NOT_ATTRIBUTED',
                    source_label: 'NTSL-settled NPCI row no rule explains',
                    rrn: '4195000077',
                    amount: '100.00',
                    detail: 'No G rule explains it.',
                  },
                ],
              ),
            )
          : url.includes('kind=sop')
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
    // A passing self-check stays under the collapsed "Details" (U1 5.4).
    expect(screen.queryByText('NTSL self-check did not pass')).toBeNull();
    const details = screen.getByRole('heading', { name: 'Details' });
    expect(details.closest('details')!.open).toBe(false);
    await userEvent.click(details);
    expect(screen.getByText(/^Versions used: Tolerance v/)).toBeTruthy();
    expect(
      within(screen.getByRole('table', { name: 'NTSL self-check per cycle' })).getAllByText('PASS'),
    ).toHaveLength(12);

    // Only the unexplained entries lead (U3 5.3); the source code column is not shown.
    const unexplained = screen.getByRole('table', { name: 'Unexplained entries' });
    expect(await within(unexplained).findByText('NTSL-settled NPCI row no rule explains')).toBeTruthy();
    expect(within(unexplained).queryByText('NPCI_NOT_ATTRIBUTED')).toBeNull();
    expect(fetch.mock.calls.some((c) => String(c[0]).includes('kind=npci'))).toBe(false);

    // Every contributing row, one click away (loaded when opened).
    await userEvent.click(screen.getByRole('heading', { name: 'All contributing rows' }));
    const tx = await screen.findByRole('table', { name: 'Contributing transactions' });
    expect(await within(tx).findByText('-1,070.50')).toBeTruthy();
    await userEvent.click(screen.getByRole('radio', { name: /SOP check items/ }));
    expect(await within(tx).findByText('G07')).toBeTruthy();
    const sopCall = fetch.mock.calls.map((c) => String(c[0])).find((u) => u.includes('kind=sop'));
    expect(sopCall).toMatch(/^\/api\/v1\/upi\/dates\/2026-07-14\/settlement\/transactions\/\?kind=sop/);
  });

  it('shows a failing NTSL self-check on top (U1 5.4)', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/settlement/transactions/': txPage('npci', [], []),
        '/settlement/': { ...SETTLEMENT, self_check: { ...SETTLEMENT.self_check, pass: 11, fail: 1 } },
        '/meta/': META,
      }),
    );
    renderAt(
      '/:module/dates/:businessDate/settlement',
      '/upi/dates/2026-07-14/settlement',
      <SettlementPage />,
    );
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('NTSL self-check did not pass')).toBeTruthy();
    expect(within(alert).getByText(/11 of 12 cycles PASS \(1 FAIL\)/)).toBeTruthy();
  });

  it('hides everything GST on fees by default (owner 2026-11-06; lib/featureFlags)', async () => {
    const gstRow = {
      fee_category: 'PSP_FEE',
      gst_category: 'PSP_FEE_GST',
      side: 'CREDIT' as const,
      fee: '1000.00',
      fee_txn_count: 120,
      expected_gst: '180.00',
      ntsl_gst: '175.00',
      difference: '5.00',
      tolerance: '1.00',
      within: false,
      note: 'OUTSIDE_TOLERANCE' as const,
    };
    const results = SETTLEMENT.self_check.results.map((r, i) =>
      i === 0
        ? { ...r, gst_warning: true, gst_check: { rate: '0.18', tolerance: '1.00', rows: [gstRow] } }
        : i === 1
          ? { ...r, gst_check: null }
          : r,
    );
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/settlement/transactions/': txPage('npci', [], []),
        '/settlement/': { ...SETTLEMENT, self_check: { ...SETTLEMENT.self_check, gst_warnings: 1, results } },
        '/meta/': META,
      }),
    );
    renderAt(
      '/:module/dates/:businessDate/settlement',
      '/upi/dates/2026-07-14/settlement',
      <SettlementPage />,
    );
    // The self-check table is there (under Details), as before the GST work.
    expect(await screen.findByText('Final net diff')).toBeTruthy();
    expect(screen.queryByRole('status')).toBeNull(); // no top notice
    expect(screen.queryByText(/GST on fees/)).toBeNull(); // column, summary and not-checked lines
    expect(screen.queryByText('GST warning')).toBeNull();
    expect(screen.queryByText('Not checked')).toBeNull();
    expect(screen.queryByText(/GST rows/)).toBeNull();
    expect(screen.queryByText('Payer PSP fee')).toBeNull();
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
    expect(selfCheckLine({ cycles: 0, pass: 0, fail: 0, missing: 0, all_pass: false, gst_warnings: 0, results: [] })).toBe(
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

  it('keeps Exception cases highlighted on "Assigned to me", not on "Awaiting my approval"', () => {
    const cases = MENU.find((i) => i.key === 'cases')!;
    const on = (search: string) => isActive('/upi/cases', '/upi/cases', search, false, cases.alsoMine);
    expect(on('')).toBe(true);
    expect(on('?mine=assigned')).toBe(true);
    expect(on('?mine=approval')).toBe(false);
  });
});
