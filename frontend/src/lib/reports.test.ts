/**
 * Phase 6 slice 6.1: report catalogue gating (R2), capability fallback,
 * business-date range rules (contract RR1), amount sums, module switch.
 */
import { describe, expect, it, vi } from 'vitest';

import type { RoleName } from '../api/types';
import { switchModuleHref } from '../layout/AppLayout';
import { makeMe, permissionsFor } from '../test/fixtures';
import {
  addDays,
  isIsoDate,
  MAX_RANGE_DAYS,
  presetRange,
  rangeDays,
  rangeError,
  rangeMonths,
  rangeFromParams,
  reportsFor,
  REPORTS,
  sumAmounts,
} from './reports';
import { allows, menuFor } from './roles';

const ALL: RoleName[] = ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'];
const reportKeys = (roles: RoleName[], reportCaps = false) =>
  reportsFor({ permissions: permissionsFor(roles, { reportCaps }) }, 'upi').map((r) => r.key);

describe('report gating by the R2 matrix', () => {
  it.each([false, true])(
    'the same reports per role with and without the server reports.* keys (%s)',
    (sent) => {
      const common = ['reconciliation', 'settlement', 'npci-files', 'volume', 'exceptions', 'ageing'];
      expect(reportKeys(['Administrator'], sent)).toEqual([...common, 'approvals', 'user-activity']);
      expect(reportKeys(['Auditor'], sent)).toEqual([...common, 'approvals', 'user-activity']);
      expect(reportKeys(['Checker'], sent)).toEqual([...common, 'approvals']);
      expect(reportKeys(['Maker'], sent)).toEqual(common);
      expect(reportKeys(['Operations User'], sent)).toEqual(common);
    },
  );

  it('no role: no reports and no Reports menu', () => {
    expect(reportKeys([])).toEqual([]);
    expect(menuFor(makeMe([])).map((i) => i.key)).not.toContain('reports');
  });

  it('every report is available since slices 6.2-6.4; addresses per report', () => {
    const entries = reportsFor({ permissions: permissionsFor(ALL) }, 'UPI');
    expect(entries.every((r) => r.available)).toBe(true);
    expect(entries.find((r) => r.key === 'volume')?.href).toBe('/upi/reports/volume');
    expect(entries.find((r) => r.key === 'exceptions')?.href).toBe('/upi/reports/exceptions');
    expect(entries.find((r) => r.key === 'ageing')?.href).toBe('/upi/reports/ageing');
    expect(entries.find((r) => r.key === 'approvals')?.href).toBe('/upi/reports/approvals');
    // No priority in any report (owner 2026-11-08).
    expect(REPORTS.some((r) => /priority/i.test(r.description))).toBe(false);
    expect(entries.find((r) => r.key === 'npci-files')?.href).toBe('/upi/reports/npci-files');
    expect(entries.find((r) => r.key === 'reconciliation')?.href).toBe('/upi/results');
    expect(entries.find((r) => r.key === 'settlement')?.href).toBe('/upi/settlement');
    // User activity is not module-scoped (logins are global; contract B7).
    expect(entries.find((r) => r.key === 'user-activity')?.href).toBe('/reports/user-activity');
    expect(REPORTS).toHaveLength(8);
  });

  it('an earlier build (slice 6.1) hides the reports built later', () => {
    const entries = reportsFor({ permissions: permissionsFor(ALL) }, 'upi', { builtSlice: 6.1 });
    expect(entries.filter((r) => r.available).map((r) => r.key)).toEqual([
      'reconciliation',
      'settlement',
      'npci-files',
    ]);
  });
});

describe('allows(): fallback only while the server does not send the key', () => {
  it('falls back to existing capabilities when the key is absent', () => {
    const checker = permissionsFor(['Checker']);
    expect('reports.approvals' in checker).toBe(false);
    expect(allows(checker, 'reports.approvals')).toBe(true); // via cases.approve
    expect(allows(checker, 'reports.user_activity')).toBe(false);
    expect(allows(permissionsFor(['Auditor']), 'reports.user_activity')).toBe(true); // via audit.view
  });

  it("uses the server's value once sent, even against the fallback", () => {
    const perms = { ...permissionsFor(['Checker']), 'reports.approvals': false };
    expect(allows(perms, 'reports.approvals')).toBe(false);
    const ops = { ...permissionsFor(['Operations User']), 'reports.user_activity': true };
    expect(allows(ops, 'reports.user_activity')).toBe(true);
  });

  it('existing capabilities have no fallback', () => {
    expect(allows(permissionsFor(['Maker']), 'audit.view')).toBe(false);
    expect(allows(undefined, 'results.view')).toBe(false);
  });
});

describe('menu: Reports, with the all-dates entries inside it (R3; U1 5.5)', () => {
  it('every role gets Reports; the all-dates results and settlement are reached from it, not the menu', () => {
    for (const role of ALL) {
      const keys = menuFor(makeMe([role])).map((i) => i.key);
      expect(keys).toContain('reports');
      expect(keys).not.toContain('results-all');
      expect(keys).not.toContain('settlement-all');
      const hrefs = reportsFor(makeMe([role]), 'upi')
        .filter((r) => r.available)
        .map((r) => r.href);
      expect(hrefs.slice(0, 3)).toEqual(['/upi/results', '/upi/settlement', '/upi/reports/npci-files']);
      expect(hrefs).toContain('/upi/reports/ageing');
    }
  });

  it('a Phase 5 build (slice 4) has no Reports item', () => {
    const keys = menuFor(makeMe(['Administrator']), { builtSlice: 4 }).map((i) => i.key);
    expect(keys).not.toContain('reports');
  });
});

describe('business-date range (contract RR1)', () => {
  it('checks real calendar dates', () => {
    expect(isIsoDate('2026-07-14')).toBe(true);
    expect(isIsoDate('2026-02-30')).toBe(false);
    expect(isIsoDate('14-07-2026')).toBe(false);
    expect(isIsoDate(null)).toBe(false);
  });

  it('counts both ends and crosses months', () => {
    expect(rangeDays({ date_from: '2026-07-14', date_to: '2026-07-14' })).toBe(1);
    expect(rangeDays({ date_from: '2026-07-01', date_to: '2026-09-30' })).toBe(92);
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
  });

  it('presets end at the latest business date (yesterday, IST), not today', () => {
    // On 23 Oct the latest business date is 22 Oct.
    expect(presetRange('this_month', '2026-10-22')).toEqual({
      date_from: '2026-10-01',
      date_to: '2026-10-22',
    });
    expect(presetRange('last_month', '2026-10-22')).toEqual({
      date_from: '2026-09-01',
      date_to: '2026-09-30',
    });
    expect(presetRange('last_month', '2027-01-05')).toEqual({
      date_from: '2026-12-01',
      date_to: '2026-12-31',
    });
    // The 7 business dates ending yesterday.
    expect(presetRange('last_7_days', '2026-10-03')).toEqual({
      date_from: '2026-09-27',
      date_to: '2026-10-03',
    });
  });

  it('on 1 Nov, "This month" is October and "Last month" is September', () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    try {
      vi.setSystemTime(new Date('2026-11-01T06:00:00Z')); // 1 Nov 11:30 IST
      expect(presetRange('this_month')).toEqual({ date_from: '2026-10-01', date_to: '2026-10-31' });
      expect(presetRange('last_month')).toEqual({ date_from: '2026-09-01', date_to: '2026-09-30' });
      expect(presetRange('last_7_days')).toEqual({ date_from: '2026-10-25', date_to: '2026-10-31' });
      // 1 Jan: the latest business date is 31 Dec of the year before.
      vi.setSystemTime(new Date('2027-01-01T06:00:00Z'));
      expect(presetRange('this_month')).toEqual({ date_from: '2026-12-01', date_to: '2026-12-31' });
      expect(presetRange('last_month')).toEqual({ date_from: '2026-11-01', date_to: '2026-11-30' });
    } finally {
      vi.useRealTimers();
    }
  });

  it('refuses incomplete, reversed and over-long ranges', () => {
    expect(rangeError('2026-07-01', null)).toBe('Give both dates.');
    expect(rangeError('2026-07-31', '2026-07-01')).toMatch(/before/);
    expect(rangeError('2026-07-01', '2026-09-30')).toBeNull(); // 92 days
    expect(rangeError('2026-07-01', '2026-10-01')).toBe(
      `At most ${MAX_RANGE_DAYS} days per report; narrow the dates.`,
    );
    expect(rangeError('2026-07-01', '2026-13-01')).toMatch(/DD-MMM-YYYY/);
  });

  it('the volume report by month counts whole months, at most 24 (B3)', () => {
    expect(rangeMonths({ date_from: '2026-07-14', date_to: '2026-08-02' })).toBe(2);
    expect(rangeError('2025-01-01', '2026-12-31', { maxMonths: 24 })).toBeNull();
    expect(rangeError('2025-01-01', '2027-01-01', { maxMonths: 24 })).toBe(
      'At most 24 months per report; narrow the dates.',
    );
    // 92 days does not apply by month.
    expect(rangeError('2026-01-01', '2026-10-31', { maxMonths: 24 })).toBeNull();
  });

  it('reads the address: the month of the latest business date when both dates are absent', () => {
    expect(rangeFromParams(new URLSearchParams(''), '2026-10-22')).toEqual({
      range: { date_from: '2026-10-01', date_to: '2026-10-22' },
      defaulted: true,
      error: null,
    });
    expect(
      rangeFromParams(new URLSearchParams('date_from=2026-07-14&date_to=2026-07-31'), '2026-10-23').range,
    ).toEqual({
      date_from: '2026-07-14',
      date_to: '2026-07-31',
    });
    const bad = rangeFromParams(new URLSearchParams('date_from=2026-01-01&date_to=2026-07-31'), '2026-10-23');
    expect(bad.range).toBeNull();
    expect(bad.error).toMatch(/92 days/);
  });
});

describe('sumAmounts (no floats)', () => {
  it('adds 2-decimal strings exactly and skips blanks', () => {
    expect(sumAmounts(['0.10', '0.20'])).toBe('0.30');
    expect(sumAmounts(['54613083.81', '-1070.50', null, undefined, '', 'n/a'])).toBe('54612013.31');
    expect(sumAmounts(['-35.00', '10.5'])).toBe('-24.50');
    expect(sumAmounts([])).toBe('0.00');
  });
});

describe('module switch keeps a report and its dates', () => {
  it('keeps the report path and the address filters', () => {
    expect(switchModuleHref('/upi/results', '?date_from=2026-07-01&date_to=2026-07-31', 'imps')).toBe(
      '/imps/results?date_from=2026-07-01&date_to=2026-07-31',
    );
    expect(switchModuleHref('/upi/reports', '', 'imps')).toBe('/imps/reports');
    expect(switchModuleHref('/upi/settlement', '?page=2', 'imps')).toBe('/imps/settlement?page=2');
  });

  it('other screens keep only their section, as before', () => {
    expect(switchModuleHref('/upi/cases/123', '?x=1', 'imps')).toBe('/imps/cases');
    expect(switchModuleHref('/upi/dates/2026-07-14/settlement', '', 'imps')).toBe('/imps/dates');
    expect(switchModuleHref('/upi', '', 'imps')).toBe('/imps');
  });
});
