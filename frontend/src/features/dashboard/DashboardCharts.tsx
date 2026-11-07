/**
 * Dashboard charts (UI refinements U5, 8.3; owner decisions 2026-11-07):
 * exactly four charts over the dashboard's dates (the last 30 business dates
 * with files or runs, contract F2), above the tables. No filters, drill-downs
 * or chart options - management reporting belongs to the separate MIS app.
 * Open cases by age stays a table (DashboardPage).
 *
 *   1. Tallied / not tallied per business date   (dates[].settlement)
 *   2. Exceptions by reply type per business date (dates[].exceptions)
 *   3. Transaction volume per business date      (dates[].volume)
 *   4. NPCI (SSKUPI) files per business date      (dates[].sskupi)
 *
 * Everything comes from GET /dashboard/; nothing is computed that the server
 * did not send. Each chart has a one-line text summary (screen readers, and
 * the numbers at a glance) and an empty state when there is nothing to draw.
 * Amounts stay strings and go through formatRupees (never a float).
 */
import { BarChart } from '@mantine/charts';
import { Paper, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import type { MantineColor } from '@mantine/core';
import type { ReactNode } from 'react';

import type { Dashboard, DashboardDate } from '../../api/types';
import { formatCount, formatDate, formatRupees } from '../../lib/format';

/** Fixed colours per reply type, so a type keeps its colour from day to day. */
const REPLY_COLORS: Record<string, MantineColor> = {
  DRC: 'blue.6',
  TCC: 'cyan.6',
  RET: 'grape.6',
  RRC: 'orange.6',
  NEEDS_REVIEW: 'yellow.6',
  SETTLEMENT: 'red.6',
};
const SPARE_COLORS: MantineColor[] = ['teal.6', 'indigo.6', 'pink.6', 'lime.6'];

/** "2026-07-14" -> "14-Jul" for the axis (the tooltip shows the full date). */
const axisDay = (iso: string) => formatDate(iso).slice(0, 6);

type Row = { day: string; d: DashboardDate } & Record<string, number | string | DashboardDate>;

const baseRow = (d: DashboardDate) => ({ day: axisDay(d.business_date), d });

// ---------------------------------------------------------------- 1. settlement

export function settlementRows(dates: DashboardDate[]): Row[] {
  return dates.map((d) => ({
    ...baseRow(d),
    tallied: d.settlement?.tallied ? 1 : 0,
    not_tallied: d.settlement && !d.settlement.tallied ? 1 : 0,
    no_result: d.settlement ? 0 : 1,
  }));
}

export function settlementSummary(dates: DashboardDate[]): string {
  const tallied = dates.filter((d) => d.settlement?.tallied).length;
  const notTallied = dates.filter((d) => d.settlement && !d.settlement.tallied).length;
  const none = dates.length - tallied - notTallied;
  return (
    `Tallied on ${formatCount(tallied)} of ${formatCount(dates.length)} date(s), ` +
    `not tallied on ${formatCount(notTallied)}` +
    (none ? `, no settlement result yet on ${formatCount(none)}.` : '.')
  );
}

// ---------------------------------------------------------------- 2. exceptions

const replyCount = (d: DashboardDate, type: string) =>
  d.exceptions[type as keyof DashboardDate['exceptions']] ?? 0;

/** Reply types with at least one exception in the range, in the server's order. */
export function replyTypesPresent(data: Dashboard): Dashboard['reply_types'] {
  return data.reply_types.filter((t) => data.dates.some((d) => replyCount(d, t.value) > 0));
}

export function exceptionRows(data: Dashboard): Row[] {
  const types = replyTypesPresent(data);
  return data.dates.map((d) => ({
    ...baseRow(d),
    ...Object.fromEntries(types.map((t) => [t.value, replyCount(d, t.value)])),
  }));
}

export function exceptionsSummary(data: Dashboard): string {
  const types = replyTypesPresent(data);
  const total = data.dates.reduce((n, d) => n + d.exceptions.total, 0);
  if (total === 0) return 'No exceptions on these dates.';
  const parts = types.map(
    (t) => `${t.label} ${formatCount(data.dates.reduce((n, d) => n + replyCount(d, t.value), 0))}`,
  );
  return `${formatCount(total)} exception(s): ${parts.join(', ')}.`;
}

// ---------------------------------------------------------------- 3. volume

export function volumeRows(dates: DashboardDate[]): Row[] {
  return dates.map((d) => ({ ...baseRow(d), transactions: d.volume.transactions }));
}

export function volumeSummary(dates: DashboardDate[]): string {
  const total = dates.reduce((n, d) => n + d.volume.transactions, 0);
  if (total === 0) return 'No transactions on these dates.';
  const top = dates.reduce((a, b) => (b.volume.transactions > a.volume.transactions ? b : a));
  return (
    `${formatCount(total)} transaction(s) on ${formatCount(dates.length)} date(s); ` +
    `highest ${formatCount(top.volume.transactions)} on ${formatDate(top.business_date)}.`
  );
}

// ---------------------------------------------------------------- 4. NPCI files

export function npciRows(dates: DashboardDate[]): Row[] {
  return dates.map((d) => {
    const files = d.sskupi?.file_count ?? 0;
    const notDownloaded = d.sskupi?.not_downloaded ?? 0;
    return { ...baseRow(d), downloaded: files - notDownloaded, not_downloaded: notDownloaded };
  });
}

export function npciSummary(dates: DashboardDate[]): string {
  const files = dates.reduce((n, d) => n + (d.sskupi?.file_count ?? 0), 0);
  if (files === 0) return 'No SSKUPI file was generated for these dates.';
  const notDownloaded = dates.reduce((n, d) => n + (d.sskupi?.not_downloaded ?? 0), 0);
  return (
    `${formatCount(files)} file(s) generated, ${formatCount(files - notDownloaded)} downloaded, ` +
    `${formatCount(notDownloaded)} not downloaded.`
  );
}

// ---------------------------------------------------------------- shared parts

/** The tooltip box: full date plus plain lines, in the app's formats. */
function Tip({ active, payload, lines }: TipProps & { lines: (d: DashboardDate) => ReactNode[] }) {
  const d = (payload?.[0]?.payload as Row | undefined)?.d;
  if (!active || !d) return null;
  return (
    <Paper withBorder shadow="sm" p="xs" radius="sm">
      <Text size="sm" fw={600}>
        {formatDate(d.business_date)}
      </Text>
      {lines(d).map((line, i) => (
        <Text size="xs" key={i}>
          {line}
        </Text>
      ))}
    </Paper>
  );
}
type TipProps = { active?: boolean; payload?: readonly { payload?: unknown }[] };

const tip = (lines: (d: DashboardDate) => ReactNode[]) => ({
  content: (p: TipProps) => <Tip {...p} lines={lines} />,
});

function ChartCard({
  id,
  title,
  summary,
  empty,
  children,
}: {
  id: string;
  title: string;
  summary: string;
  empty: boolean;
  children: ReactNode;
}) {
  return (
    <Paper withBorder p="sm" radius="sm" component="figure" m={0} aria-labelledby={id}>
      <Stack gap={4}>
        <Title order={3} size="h5" id={id}>
          {title}
        </Title>
        <Text size="sm" c={empty ? 'dimmed' : undefined} component="figcaption">
          {summary}
        </Text>
        {!empty && children}
      </Stack>
    </Paper>
  );
}

const CHART_HEIGHT = 200;
const common = {
  h: CHART_HEIGHT,
  dataKey: 'day',
  type: 'stacked' as const,
  tickLine: 'y' as const,
  gridAxis: 'y' as const,
  withLegend: true,
  legendProps: { verticalAlign: 'bottom' as const, height: 28 },
  valueFormatter: (v: number) => formatCount(v),
  yAxisProps: { width: 64, allowDecimals: false },
};

// ---------------------------------------------------------------- the charts

export function DashboardCharts({ data }: { data: Dashboard }) {
  const dates = data.dates;
  if (dates.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        No business date with files or runs in this range, so there is nothing to chart.
      </Text>
    );
  }
  const types = replyTypesPresent(data);
  const totalExceptions = dates.reduce((n, d) => n + d.exceptions.total, 0);
  const totalTxns = dates.reduce((n, d) => n + d.volume.transactions, 0);
  const totalFiles = dates.reduce((n, d) => n + (d.sskupi?.file_count ?? 0), 0);

  return (
    <SimpleGrid cols={{ base: 1, lg: 2 }} spacing="md">
      <ChartCard
        id="chart-settlement"
        title="Settlement: tallied / not tallied"
        summary={settlementSummary(dates)}
        empty={false}
      >
        <BarChart
          {...common}
          data={settlementRows(dates)}
          withYAxis={false}
          series={[
            { name: 'tallied', label: 'Tallied', color: 'green.6' },
            { name: 'not_tallied', label: 'Not tallied', color: 'red.6' },
            { name: 'no_result', label: 'No result yet', color: 'gray.5' },
          ]}
          tooltipProps={tip((d) =>
            !d.settlement
              ? ['No settlement result yet']
              : d.settlement.tallied
                ? ['Tallied']
                : ['Not tallied', `Unexplained ${formatRupees(d.settlement.unexplained_amount)}`],
          )}
        />
      </ChartCard>

      <ChartCard
        id="chart-exceptions"
        title="Exceptions by reply type"
        summary={exceptionsSummary(data)}
        empty={totalExceptions === 0}
      >
        <BarChart
          {...common}
          data={exceptionRows(data)}
          series={types.map((t, i) => ({
            name: t.value,
            label: t.label,
            color: REPLY_COLORS[t.value] ?? SPARE_COLORS[i % SPARE_COLORS.length],
          }))}
          tooltipProps={tip((d) => [
            ...types
              .filter((t) => replyCount(d, t.value) > 0)
              .map((t) => `${t.label} ${formatCount(replyCount(d, t.value))}`),
            `Total ${formatCount(d.exceptions.total)}`,
          ])}
        />
      </ChartCard>

      <ChartCard
        id="chart-volume"
        title="Transaction volume"
        summary={volumeSummary(dates)}
        empty={totalTxns === 0}
      >
        <BarChart
          {...common}
          type="default"
          withLegend={false}
          data={volumeRows(dates)}
          series={[{ name: 'transactions', label: 'Transactions', color: 'blue.6' }]}
          tooltipProps={tip((d) => [
            `Transactions ${formatCount(d.volume.transactions)}`,
            `NPCI ${formatCount(d.volume.npci.count)} row(s), ${formatRupees(d.volume.npci.amount)}`,
            `CBS ${formatCount(d.volume.cbs.count)} row(s), ${formatRupees(d.volume.cbs.amount)}`,
          ])}
        />
      </ChartCard>

      <ChartCard
        id="chart-npci"
        title="NPCI files (SSKUPI) generated / downloaded"
        summary={npciSummary(dates)}
        empty={totalFiles === 0}
      >
        <BarChart
          {...common}
          data={npciRows(dates)}
          series={[
            { name: 'downloaded', label: 'Downloaded', color: 'green.6' },
            { name: 'not_downloaded', label: 'Not downloaded', color: 'orange.6' },
          ]}
          tooltipProps={tip((d) => {
            const files = d.sskupi?.file_count ?? 0;
            const notDl = d.sskupi?.not_downloaded ?? 0;
            const lines: string[] = files
              ? [`${formatCount(files)} generated`, `${formatCount(files - notDl)} downloaded`]
              : ['No file'];
            if (notDl) lines.push(`${formatCount(notDl)} not downloaded`);
            if (d.sskupi?.pending && notDl === 0) lines.push('Rows wait for a file');
            return lines;
          })}
        />
      </ChartCard>
    </SimpleGrid>
  );
}
