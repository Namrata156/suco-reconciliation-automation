/** Phase 6 report blocks shaped like api/reports*.py answers (checked against the dev database 2026-11-09). */
import type { ExportBlock, ReconciliationRow, ReconciliationTotals, ReportBlock } from '../api/types';

/** RR2. generated_at = 15-Jul-2026 11:30 IST. */
export function reportBlock(key: string, over: Partial<ReportBlock> = {}): ReportBlock {
  return {
    key,
    title: key,
    module: 'upi',
    filters: {},
    generated_at: '2026-07-15T06:00:00Z',
    generated_by: 'maker1',
    versions: { rule_sets: [], ntsl_mappings: [], tolerances: [] },
    ...over,
  };
}

/** RR3 with the server's formats today (CSV only); sheets as [key, label, rows]. */
export function exportBlock(
  sheets: [string, string, number][],
  { formats = ['csv'], limit = 100_000 }: { formats?: string[]; limit?: number } = {},
): ExportBlock {
  return {
    formats,
    row_count: sheets[0]?.[2] ?? 0,
    limit,
    allowed: sheets.every(([, , n]) => n <= limit),
    sheets: sheets.map(([key, label, row_count]) => ({ key, label, row_count })),
  };
}

const add = (a: Record<string, number>, b: Record<string, number>) => {
  const out = { ...a };
  for (const [k, n] of Object.entries(b)) out[k] = (out[k] ?? 0) + n;
  return out;
};

/** B1 totals summed like api/reports_daily.reconciliation_totals. */
export function reconciliationTotals(rows: ReconciliationRow[]): ReconciliationTotals {
  const t: ReconciliationTotals = {
    dates: rows.length,
    files: { expected: 0, present: 0, missing: 0, received: 0 },
    match_groups: { total: 0, by_status: {}, on_us: 0, cbs_reversed: 0 },
    results_by_category: {},
    rule_counts: {},
    cases: { created: 0, open: 0 },
    settlement: { by_status: {}, unexplained_amount: '0.00' },
  };
  let paise = 0;
  for (const r of rows) {
    for (const k of ['expected', 'present', 'missing', 'received'] as const) t.files[k] += r.files[k];
    t.match_groups.total += r.match_groups.total;
    t.match_groups.on_us += r.match_groups.on_us;
    t.match_groups.cbs_reversed += r.match_groups.cbs_reversed;
    t.match_groups.by_status = add(t.match_groups.by_status, r.match_groups.by_status);
    t.results_by_category = add(t.results_by_category, r.results_by_category);
    t.rule_counts = add(t.rule_counts, r.rule_counts);
    t.cases.created += r.cases.created;
    t.cases.open += r.cases.open;
    const s = r.settlement?.status ?? 'NONE';
    t.settlement.by_status[s] = (t.settlement.by_status[s] ?? 0) + 1;
    paise += Math.round(Number(r.settlement?.unexplained_amount ?? 0) * 100);
  }
  t.settlement.unexplained_amount = (paise / 100).toFixed(2);
  return t;
}
