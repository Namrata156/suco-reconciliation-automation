/**
 * Fee GST check on the NTSL self-check (settlement.fee_gst, owner
 * 2026-11-06): warning only, it never changes PASS / FAIL.
 *
 * The display is hidden by owner 2026-11-06 (lib/featureFlags
 * SHOW_GST_ON_FEES = false); these tests force it on so the hidden code stays
 * tested. "Hidden by default" is in pages/ResultsPages.test.tsx.
 */
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { GstCheck, GstCheckRow, SelfCheck, SelfCheckRow, SettlementDate } from '../../api/types';
import { MeProvider } from '../../auth/auth';
import { SettlementPage } from '../../pages/SettlementPage';
import { makeMe, META, renderWithProviders, routeFetch } from '../../test/fixtures';
import { gstWarningLine, SelfCheckTable } from './SelfCheckTable';

vi.mock('../../lib/featureFlags', () => ({ SHOW_GST_ON_FEES: true }));

afterEach(() => {
  vi.unstubAllGlobals();
});

const fig = { ntsl: '10.00', computed: '10.00', difference: '0.00' };
const PAIRS = ['FEE', 'SWITCHING_FEE', 'PSP_FEE', 'REBATE', 'MANDATE_FEE', 'NRP_FEE'];

const zeroRow = (fee: string, side: 'DEBIT' | 'CREDIT'): GstCheckRow => ({
  fee_category: fee,
  gst_category: `${fee}_GST`,
  side,
  fee: '0.00',
  fee_txn_count: 0,
  expected_gst: '0.00',
  ntsl_gst: '0.00',
  difference: '0.00',
  tolerance: '1.00',
  within: true,
  note: null,
});

/** 12 rows, all zero, with the given rows replaced (by category and side). */
function gst(...over: GstCheckRow[]): GstCheck {
  const rows = PAIRS.flatMap((p) => (['DEBIT', 'CREDIT'] as const).map((s) => zeroRow(p, s))).map(
    (r) => over.find((o) => o.fee_category === r.fee_category && o.side === r.side) ?? r,
  );
  return { rate: '0.18', tolerance: '1.00', rows };
}

const SWITCHING_OK: GstCheckRow = {
  ...zeroRow('SWITCHING_FEE', 'DEBIT'),
  fee: '564.56',
  fee_txn_count: 4868,
  expected_gst: '101.62',
  ntsl_gst: '101.64',
  difference: '-0.02',
};
const PSP_OFF: GstCheckRow = {
  ...zeroRow('PSP_FEE', 'CREDIT'),
  fee: '1000.00',
  fee_txn_count: 120,
  expected_gst: '180.00',
  ntsl_gst: '175.00',
  difference: '5.00',
  within: false,
  note: 'OUTSIDE_TOLERANCE',
};

const row = (id: number, cycle: string, extra: Partial<SelfCheckRow> = {}): SelfCheckRow => ({
  id,
  cycle,
  status: 'PASS',
  failures: [],
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
  gst_check: gst(SWITCHING_OK),
  checked_at: '2026-11-06T05:02:00Z',
  source: 'run',
  ...extra,
});

const check = (results: SelfCheckRow[], gst_warnings = 0): SelfCheck => ({
  cycles: results.length,
  pass: results.filter((r) => r.status === 'PASS').length,
  fail: results.filter((r) => r.status === 'FAIL').length,
  missing: results.filter((r) => r.status === 'MISSING').length,
  all_pass: results.every((r) => r.status === 'PASS'),
  gst_warnings,
  results,
});

const cycleRow = (cycle: string) => {
  const table = screen.getByRole('table', { name: 'NTSL self-check per cycle' });
  const tr = within(table)
    .getAllByRole('row')
    .find((r) => within(r).queryAllByRole('cell')[0]?.textContent === cycle);
  if (!tr) throw new Error(`no row for cycle ${cycle}`);
  return tr;
};

describe('NTSL self-check: GST on fees (warning only)', () => {
  it('shows the warning per cycle and in the summary, without changing PASS', async () => {
    const c = check(
      [row(1, '1C'), row(2, '2C', { gst_warning: true, gst_check: gst(SWITCHING_OK, PSP_OFF) })],
      1,
    );
    expect(gstWarningLine(c)).toBe('GST on fees: 1 cycle with a warning');
    renderWithProviders(<SelfCheckTable check={c} />);

    expect(screen.getByText('GST on fees: 1 cycle with a warning')).toBeTruthy();
    const warned = cycleRow('2C');
    expect(within(warned).getByText('PASS')).toBeTruthy(); // status badge unchanged
    expect(within(warned).getByText('GST warning')).toBeTruthy();
    expect(within(cycleRow('1C')).queryByText('GST warning')).toBeNull();
    expect(within(cycleRow('1C')).getByText('OK')).toBeTruthy();

    // The GST rows open per cycle.
    expect(screen.queryByRole('table', { name: 'GST on fees, cycle 2C' })).toBeNull();
    const button = within(warned).getByRole('button', { name: 'Show GST rows for cycle 2C' });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    await userEvent.click(button);
    const t = screen.getByRole('table', { name: 'GST on fees, cycle 2C' });
    expect(within(t).getByRole('columnheader', { name: 'Expected GST (18%)' })).toBeTruthy();
    expect(within(t).getByRole('columnheader', { name: 'Within Rs 1.00' })).toBeTruthy();
    // Rows with no fee and no GST are hidden: header + 2 rows.
    expect(within(t).getAllByRole('row')).toHaveLength(3);
    const off = within(t).getByText('Payer PSP fee').closest('tr')!;
    expect(within(off).getByText('Credit')).toBeTruthy();
    expect(within(off).getByText('1,000.00')).toBeTruthy();
    expect(within(off).getByText('5.00')).toBeTruthy();
    expect(within(off).getByText('No: Difference is over the tolerance')).toBeTruthy();
    expect(off.getAttribute('style') ?? '').toContain('yellow');
    const ok = within(t).getByText('NPCI switching fee').closest('tr')!;
    expect(within(ok).getByText('-0.02')).toBeTruthy();
    expect(within(ok).getByText('Yes')).toBeTruthy();
    expect(ok.getAttribute('style') ?? '').not.toContain('yellow');

    await userEvent.click(within(warned).getByRole('button', { name: 'Hide GST rows for cycle 2C' }));
    expect(screen.queryByRole('table', { name: 'GST on fees, cycle 2C' })).toBeNull();
  });

  it('shows no warning when every cycle is within', async () => {
    const c = check([row(1, '1C'), row(2, '2C')]);
    expect(gstWarningLine(c)).toBeNull();
    renderWithProviders(<SelfCheckTable check={c} />);
    expect(screen.getByText('GST on fees: no warnings.')).toBeTruthy();
    expect(screen.queryByText('GST warning')).toBeNull();
    expect(screen.queryByText(/with a warning/)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Show GST rows for cycle 1C' }));
    const t = screen.getByRole('table', { name: 'GST on fees, cycle 1C' });
    expect(within(t).getByText('NPCI switching fee')).toBeTruthy();
  });

  it('shows "Not checked" for a self-check stored before the GST check (null)', () => {
    renderWithProviders(<SelfCheckTable check={check([row(1, '1C', { gst_check: null }), row(2, '2C')])} />);
    expect(within(cycleRow('1C')).getByText('Not checked')).toBeTruthy();
    expect(within(cycleRow('1C')).queryByRole('button')).toBeNull();
    expect(within(cycleRow('1C')).getByText('PASS')).toBeTruthy();
    expect(
      screen.getByText('GST on fees: not checked for 1 cycle (checked before the GST check existed).'),
    ).toBeTruthy();
  });

  it('shows nothing to check for a MISSING cycle ({})', () => {
    const missing = row(1, '1C', {
      status: 'MISSING',
      failures: ['No NTSL lines loaded for this cycle'],
      line_count: 0,
      gst_check: {},
    });
    renderWithProviders(<SelfCheckTable check={check([missing])} />);
    const tr = cycleRow('1C');
    expect(within(tr).getByText('MISSING')).toBeTruthy();
    expect(within(tr).queryByText('Not checked')).toBeNull();
    expect(within(tr).queryByText('GST warning')).toBeNull();
    expect(within(tr).queryByRole('button')).toBeNull();
    expect(within(tr).getByTitle('No NTSL lines for this cycle').textContent).toBe('-');
    expect(screen.queryByText(/GST on fees:/)).toBeNull();
  });
});

describe('Settlement page: GST on fees notice (flag on)', () => {
  it('shows the warning on top as a warning only, not a self-check failure', async () => {
    const settlement: SettlementDate = {
      business_date: '2026-11-05',
      result: null,
      self_check: check(
        [
          row(1, '1C', { gst_warning: true, gst_check: gst(PSP_OFF) }),
          row(2, '2C', { gst_warning: true, gst_check: gst(PSP_OFF) }),
        ],
        2,
      ),
      case: null,
      earlier_results: [],
    };
    vi.stubGlobal('fetch', routeFetch({ '/settlement/': settlement, '/meta/': META }));
    renderWithProviders(
      <MeProvider me={makeMe(['Auditor'])}>
        <Routes>
          <Route path="/:module/dates/:businessDate/settlement" element={<SettlementPage />} />
        </Routes>
      </MeProvider>,
      { route: '/upi/dates/2026-11-05/settlement' },
    );
    const notice = await screen.findByRole('status');
    expect(within(notice).getByText('GST on fees: 2 cycles with a warning')).toBeTruthy();
    expect(within(notice).getByText(/does not change PASS \/ FAIL/)).toBeTruthy();
    expect(screen.queryByText('NTSL self-check did not pass')).toBeNull();
  });
});
