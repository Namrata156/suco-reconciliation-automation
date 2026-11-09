/**
 * Phase 6 reports (docs/phase6_design_draft_v1.txt, approved 2026-10-23;
 * contract docs/phase6_api_contract.txt).
 *
 * - The report catalogue shown on the Reports page, gated by the R2 role
 *   matrix through /me capabilities (lib/roles.ts allows(), with fallbacks
 *   while the server lacks the reports.* keys).
 * - The business-date range every report filters on (contract RR1): kept in
 *   the address as date_from / date_to; when both are absent, the month of
 *   the latest business date (yesterday, IST) up to it; at most 92 days.
 */
import type { Capability, Me } from '../api/types';
import { allows } from './roles';
import { latestBusinessDateIst } from './format';

// ---------------------------------------------------------------------------
// catalogue
// ---------------------------------------------------------------------------
export type ReportSlice = 6.1 | 6.2 | 6.3 | 6.4;

export interface ReportDef {
  key: string;
  title: string;
  /** One line on what it shows (the Reports page card). */
  description: string;
  /** Path under /<module>/ when scoped, else absolute. */
  to: string;
  scoped: boolean;
  capability: Capability;
  /** The Phase 6 slice that builds the screen. */
  slice: ReportSlice;
}

/** The slice of Phase 6 the running build implements. */
export const BUILT_REPORT_SLICE: ReportSlice = 6.4;

/** The 7 spec reports (design section 2), in the design's order, plus the NPCI files report (U5). */
export const REPORTS: readonly ReportDef[] = [
  {
    key: 'reconciliation',
    title: 'Daily reconciliation report - Results (all dates)',
    description:
      'How each business date reconciled: files, transactions, cases and settlement status, one row per date.',
    to: 'results',
    scoped: true,
    capability: 'results.view',
    slice: 6.1,
  },
  {
    key: 'settlement',
    title: 'Settlement report - Settlement (all dates)',
    description: "Whether each date's NPCI settlement tied to CBS, and the unexplained amount if not.",
    to: 'settlement',
    scoped: true,
    capability: 'results.view',
    slice: 6.1,
  },
  // UI refinements U5 8.1 (contract F1): not one of the 7 spec reports; replaces
  // the tracker's "all-files list page".
  {
    key: 'npci-files',
    title: 'NPCI files (SSKUPI)',
    description:
      'Every SSKUPI file generated, with rows and amount, who generated it and who downloaded it, and when.',
    to: 'reports/npci-files',
    scoped: true,
    capability: 'reports.view',
    slice: 6.1,
  },
  {
    key: 'volume',
    title: 'Daily / monthly transaction volume',
    description: 'Transactions and amounts each source file carried, per day or per month.',
    to: 'reports/volume',
    scoped: true,
    capability: 'reports.view',
    slice: 6.2,
  },
  {
    key: 'exceptions',
    title: 'Exception report',
    description: 'Exception cases for a period, open or closed, with the action taken.',
    to: 'reports/exceptions',
    scoped: true,
    capability: 'reports.view',
    slice: 6.3,
  },
  {
    key: 'ageing',
    title: 'Ageing report',
    description: 'Open cases by age bucket and rule, with the overdue counts.',
    to: 'reports/ageing',
    scoped: true,
    capability: 'reports.view',
    slice: 6.3,
  },
  {
    key: 'approvals',
    title: 'Approval report',
    description: 'Every maker-checker decision in a period: who proposed, who decided, self-approvals.',
    to: 'reports/approvals',
    scoped: true,
    capability: 'reports.approvals',
    slice: 6.3,
  },
  {
    key: 'user-activity',
    title: 'User activity report',
    description: 'Logins and work done per user in a period.',
    to: '/reports/user-activity',
    scoped: false,
    capability: 'reports.user_activity',
    slice: 6.4,
  },
];

export interface ReportEntry extends ReportDef {
  /** The screen is built in this release. */
  available: boolean;
  href: string;
}

/** The reports a user may open (R2), marked available when their screen is built. */
export function reportsFor(
  me: Pick<Me, 'permissions'>,
  moduleSlug: string,
  { builtSlice = BUILT_REPORT_SLICE }: { builtSlice?: number } = {},
): ReportEntry[] {
  return REPORTS.filter((r) => allows(me.permissions, r.capability)).map((r) => ({
    ...r,
    available: r.slice <= builtSlice,
    href: r.scoped ? `/${moduleSlug.toLowerCase()}/${r.to}` : r.to,
  }));
}

// ---------------------------------------------------------------------------
// business-date range (contract RR1)
// ---------------------------------------------------------------------------
export const MAX_RANGE_DAYS = 92;
/** B3 by month: the range is widened to whole months, at most 24 of them. */
export const MAX_RANGE_MONTHS = 24;

/** Limits of one report's range (RR1); the default is 92 days. */
export interface RangeRules {
  /** A count of whole calendar months instead of days (B3 by month). */
  maxMonths?: number;
}

export interface DateRange {
  date_from: string;
  date_to: string;
}

const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A real calendar date as YYYY-MM-DD (rejects 2026-02-30). */
export function isIsoDate(v: string | null | undefined): v is string {
  const m = ISO.exec(v ?? '');
  if (!m) return false;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const t = new Date(Date.UTC(y, mo - 1, d));
  return t.getUTCFullYear() === y && t.getUTCMonth() === mo - 1 && t.getUTCDate() === d;
}

const toUtc = (iso: string) => {
  const [y, m, d] = iso.split('-').map(Number) as [number, number, number];
  return Date.UTC(y, m - 1, d);
};
const fromUtc = (t: number) => new Date(t).toISOString().slice(0, 10);
const DAY_MS = 86_400_000;

export const addDays = (iso: string, n: number) => fromUtc(toUtc(iso) + n * DAY_MS);

/** Days in the range, both ends included (14 Jul - 14 Jul = 1). */
export const rangeDays = (r: DateRange) => Math.round((toUtc(r.date_to) - toUtc(r.date_from)) / DAY_MS) + 1;

export type PresetKey = 'this_month' | 'last_month' | 'last_7_days';

export const PRESETS: readonly { key: PresetKey; label: string }[] = [
  { key: 'this_month', label: 'This month' },
  { key: 'last_month', label: 'Last month' },
  { key: 'last_7_days', label: 'Last 7 days' },
];

/**
 * A preset range relative to the latest business date (yesterday, IST; on
 * calendar day D ops reconcile D-1):
 *   - this_month: the month of the latest business date, up to it (on 1 Nov
 *     that is 1-31 Oct);
 *   - last_month: the whole month before that;
 *   - last_7_days: the 7 business dates ending with the latest one.
 */
export function presetRange(key: PresetKey, latest = latestBusinessDateIst()): DateRange {
  const [y, m] = latest.split('-').map(Number) as [number, number];
  if (key === 'this_month') return { date_from: `${latest.slice(0, 7)}-01`, date_to: latest };
  if (key === 'last_month') {
    const first = fromUtc(Date.UTC(y, m - 2, 1));
    const last = fromUtc(Date.UTC(y, m - 1, 0));
    return { date_from: first, date_to: last };
  }
  return { date_from: addDays(latest, -6), date_to: latest };
}

/** Calendar months the range touches (14 Jul - 2 Aug = 2). */
export const rangeMonths = (r: DateRange) => {
  const [y1, m1] = r.date_from.split('-').map(Number) as [number, number];
  const [y2, m2] = r.date_to.split('-').map(Number) as [number, number];
  return (y2 - y1) * 12 + (m2 - m1) + 1;
};

/** Why a range cannot be used, or null when it can (contract RR1). */
export function rangeError(
  from: string | null | undefined,
  to: string | null | undefined,
  rules: RangeRules = {},
): string | null {
  if (!from || !to) return 'Give both dates.';
  if (!isIsoDate(from) || !isIsoDate(to)) return 'Enter the dates as DD-MMM-YYYY.';
  if (to < from) return 'The "To" date is before the "From" date.';
  if (rules.maxMonths) {
    return rangeMonths({ date_from: from, date_to: to }) > rules.maxMonths
      ? `At most ${rules.maxMonths} months per report; narrow the dates.`
      : null;
  }
  if (rangeDays({ date_from: from, date_to: to }) > MAX_RANGE_DAYS) {
    return `At most ${MAX_RANGE_DAYS} days per report; narrow the dates.`;
  }
  return null;
}

/**
 * The range in the address: both absent -> this month of the latest
 * business date, ending with it (contract RR1); otherwise as given, or an
 * error to show instead of loading anything.
 */
export function rangeFromParams(
  params: URLSearchParams,
  latest = latestBusinessDateIst(),
  rules: RangeRules = {},
): { range: DateRange; defaulted: boolean; error: null } | { range: null; defaulted: false; error: string } {
  const from = params.get('date_from');
  const to = params.get('date_to');
  if (!from && !to) return { range: presetRange('this_month', latest), defaulted: true, error: null };
  const error = rangeError(from, to, rules);
  if (error) return { range: null, defaulted: false, error };
  return { range: { date_from: from!, date_to: to! }, defaulted: false, error: null };
}

// ---------------------------------------------------------------------------
// sums of 2-decimal amount strings, without floats (format.ts rule)
// ---------------------------------------------------------------------------
const AMOUNT = /^(-)?(\d+)(?:\.(\d{1,2}))?$/;

/** Sum of amount strings ("1070.50", "-35.00"); unparsable / null values are skipped. */
export function sumAmounts(values: readonly (string | null | undefined)[]): string {
  let paise = 0n;
  for (const v of values) {
    const m = AMOUNT.exec((v ?? '').trim());
    if (!m) continue;
    const n = BigInt(m[2] ?? '0') * 100n + BigInt(((m[3] ?? '') + '00').slice(0, 2));
    paise += m[1] ? -n : n;
  }
  const neg = paise < 0n;
  const abs = neg ? -paise : paise;
  return `${neg ? '-' : ''}${abs / 100n}.${String(abs % 100n).padStart(2, '0')}`;
}
