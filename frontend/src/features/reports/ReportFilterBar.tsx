/**
 * The filter bar every report shares (design 5.3, contract RR1):
 *   - business-date range From / To, kept in the address (date_from /
 *     date_to) so a link or bookmark gives the same report; when
 *     both are absent the month of the latest business date (yesterday, IST)
 *     up to it; at most 92 days; the pickers stop at the latest business date;
 *   - presets "This month", "Last month", "Last 7 days" (applied at once),
 *     all relative to the latest business date, not today;
 *   - the module, read from the header switcher (not a separate filter);
 *   - the report's own filters (`children`), which apply as they change.
 * The dates apply with "Apply", so a half-entered range never loads.
 *
 * Phase 6 options (RangeOptions): reports on an event date (approvals on the
 * decision date, user activity on the event date) end at today, not at the
 * latest business date; the volume report by month allows 24 months.
 */
import { Button, Group, Text } from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';

import { useMe } from '../../auth/auth';
import { useModuleCode } from '../../hooks/module';
import { latestBusinessDateIst, todayIst } from '../../lib/format';
import {
  PRESETS,
  presetRange,
  rangeError,
  rangeFromParams,
  type DateRange,
  type RangeRules,
} from '../../lib/reports';

export interface RangeOptions {
  /**
   * "business" (default): business dates, ending at the latest one (yesterday);
   * "event": an event date (a decision, a login), which may be today.
   */
  basis?: 'business' | 'event';
  /** B3 by month: up to this many whole months instead of 92 days. */
  maxMonths?: number;
}

const rulesOf = (o: RangeOptions): RangeRules => (o.maxMonths ? { maxMonths: o.maxMonths } : {});

/** The range in the address (or the latest date's month), and a setter that keeps the other filters. */
export function useReportRange(options: RangeOptions = {}) {
  const [params, setParams] = useSearchParams();
  const latest = options.basis === 'event' ? todayIst() : latestBusinessDateIst();
  const maxMonths = options.maxMonths;
  const resolved = useMemo(
    () => rangeFromParams(params, latest, maxMonths ? { maxMonths } : {}),
    [params, latest, maxMonths],
  );
  const setRange = (r: DateRange) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      next.set('date_from', r.date_from);
      next.set('date_to', r.date_to);
      next.delete('page');
      return next;
    });
  return { ...resolved, setRange, params, latest };
}

/** The current module's display name ("UPI"), from /me. */
export function useModuleName(): string {
  const me = useMe();
  const slug = useModuleCode();
  const m = me.modules.find((x) => x.slug === slug);
  return m?.code ?? slug.toUpperCase();
}

export interface ReportFilterBarProps extends RangeOptions {
  children?: ReactNode;
  /** The date inputs' label ("Business date", "Decision date", "Date"). */
  dateLabel?: string;
  /** Show "Module: UPI (change it in the header)"; off for a report of all modules. */
  showModule?: boolean;
}

export function ReportFilterBar(props: ReportFilterBarProps) {
  const [params] = useSearchParams();
  // A new address (back button, a preset, a link) restarts the inputs from it.
  const key = `${params.get('date_from') ?? ''}|${params.get('date_to') ?? ''}`;
  return <RangeForm key={key} {...props} />;
}

function RangeForm({
  children,
  dateLabel = 'Business date',
  showModule = true,
  ...options
}: ReportFilterBarProps) {
  const { range, setRange, params, latest } = useReportRange(options);
  const moduleName = useModuleName();
  // The inputs start from the address; an invalid address leaves them as typed there.
  const [from, setFrom] = useState<string | null>(range?.date_from ?? params.get('date_from'));
  const [to, setTo] = useState<string | null>(range?.date_to ?? params.get('date_to'));
  const [error, setError] = useState<string | null>(null);

  const apply = (e: FormEvent) => {
    e.preventDefault();
    const problem = rangeError(from, to, rulesOf(options));
    setError(problem);
    if (!problem) setRange({ date_from: from!, date_to: to! });
  };
  const preset = (key: (typeof PRESETS)[number]['key']) => {
    const r = presetRange(key, latest);
    setFrom(r.date_from);
    setTo(r.date_to);
    setError(null);
    setRange(r);
  };

  return (
    <section aria-label="Report filters" className="no-print">
      <form onSubmit={apply} noValidate>
        <Group gap="xs" align="flex-end" wrap="wrap">
          <DateInput
            label={`${dateLabel} from`}
            valueFormat="DD-MMM-YYYY"
            value={from}
            onChange={setFrom}
            maxDate={latest}
            w={170}
            error={!!error}
          />
          <DateInput
            label={`${dateLabel} to`}
            valueFormat="DD-MMM-YYYY"
            value={to}
            onChange={setTo}
            maxDate={latest}
            w={170}
            error={!!error}
          />
          <Button type="submit">Apply</Button>
          {PRESETS.map((p) => (
            <Button key={p.key} variant="subtle" onClick={() => preset(p.key)}>
              {p.label}
            </Button>
          ))}
          {children}
          {showModule && (
            <Text size="sm" c="dimmed" ml="auto">
              Module: {moduleName} (change it in the header)
            </Text>
          )}
        </Group>
        {error && (
          <Text size="sm" c="red" role="alert" mt={4}>
            {error}
          </Text>
        )}
      </form>
    </section>
  );
}
