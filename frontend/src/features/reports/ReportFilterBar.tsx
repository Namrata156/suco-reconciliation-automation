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
 */
import { Button, Group, Text } from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { useSearchParams } from 'react-router';

import { useMe } from '../../auth/auth';
import { useModuleCode } from '../../hooks/module';
import { latestBusinessDateIst } from '../../lib/format';
import { PRESETS, presetRange, rangeError, rangeFromParams, type DateRange } from '../../lib/reports';

/** The range in the address (or the latest business date's month), and a setter that keeps the other filters. */
export function useReportRange() {
  const [params, setParams] = useSearchParams();
  const latest = latestBusinessDateIst();
  const resolved = useMemo(() => rangeFromParams(params, latest), [params, latest]);
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

export function ReportFilterBar({ children }: { children?: ReactNode }) {
  const [params] = useSearchParams();
  // A new address (back button, a preset, a link) restarts the inputs from it.
  const key = `${params.get('date_from') ?? ''}|${params.get('date_to') ?? ''}`;
  return <RangeForm key={key}>{children}</RangeForm>;
}

function RangeForm({ children }: { children?: ReactNode }) {
  const { range, setRange, params, latest } = useReportRange();
  const moduleName = useModuleName();
  // The inputs start from the address; an invalid address leaves them as typed there.
  const [from, setFrom] = useState<string | null>(range?.date_from ?? params.get('date_from'));
  const [to, setTo] = useState<string | null>(range?.date_to ?? params.get('date_to'));
  const [error, setError] = useState<string | null>(null);

  const apply = (e: FormEvent) => {
    e.preventDefault();
    const problem = rangeError(from, to);
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
            label="Business date from"
            valueFormat="DD-MMM-YYYY"
            value={from}
            onChange={setFrom}
            maxDate={latest}
            w={170}
            error={!!error}
          />
          <DateInput
            label="Business date to"
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
          <Text size="sm" c="dimmed" ml="auto">
            Module: {moduleName} (change it in the header)
          </Text>
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
