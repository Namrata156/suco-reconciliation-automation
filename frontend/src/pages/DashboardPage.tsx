/**
 * Dashboard (UI refinements U5, 8.3; contract F2). Four charts on top
 * (@mantine/charts, owner decision 2026-11-07; features/dashboard), then
 * the same numbers as plain tables: per business date (volume, exceptions by
 * reply type, settlement, open cases, SSKUPI file) and the open cases by age
 * now (a table only, no chart).
 *
 * Without dates in the address the server answers for the last 30 business
 * dates that have files or runs; the date bar narrows it (at most 92 days).
 */
import { Alert, Anchor, Stack, Table, Text, Title } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';

import { ApiError, getDashboard } from '../api/client';
import type { Dashboard } from '../api/types';
import { useMe } from '../auth/auth';
import { SettlementStatusBadge } from '../components/Badges';
import { DateStatusBadge } from '../components/DayStatus';
import { DashboardCharts } from '../features/dashboard/DashboardCharts';
import { ReportFilterBar, useReportRange } from '../features/reports/ReportFilterBar';
import { ReportFrame } from '../features/reports/ReportFrame';
import { useModuleCode } from '../hooks/module';
import { formatCount, formatDate, formatRupees } from '../lib/format';
import { spaLink } from '../lib/links';
import { allows } from '../lib/roles';

/** "DRC 179, RET 18, Needs review 606" (only the types present). */
export function exceptionsLine(d: Dashboard['dates'][number], types: Dashboard['reply_types']): string {
  return types
    .filter((t) => (d.exceptions[t.value as keyof typeof d.exceptions] ?? 0) > 0)
    .map((t) => `${t.label} ${formatCount(d.exceptions[t.value as keyof typeof d.exceptions])}`)
    .join(', ');
}

export function DashboardPage() {
  const me = useMe();
  const module = useModuleCode();
  const allowed = allows(me.permissions, 'results.view');
  const { range, error: rangeErr, params } = useReportRange();
  // Dates only when the user chose them; otherwise the server's last 30 business dates.
  const chosen = params.get('date_from') && params.get('date_to') && range ? range : null;
  const query = useQuery({
    queryKey: ['dashboard', module, chosen],
    queryFn: () => getDashboard(module, chosen ?? {}),
    placeholderData: keepPreviousData,
    enabled: allowed && !rangeErr,
  });
  const data = query.data;

  if (!allowed) {
    return (
      <Alert color="yellow" title="Dashboard" role="alert">
        Your role does not include the reconciliation results.
      </Alert>
    );
  }

  const shown = data ? { date_from: data.range.date_from, date_to: data.range.date_to } : chosen;

  return (
    <ReportFrame
      title="Dashboard"
      description="The last business dates at a glance: volume, exceptions, settlement, cases and the SSKUPI file."
      range={shown}
      updatedAt={query.dataUpdatedAt}
      onRefresh={() => void query.refetch()}
      refreshing={query.isFetching}
      filters={<ReportFilterBar />}
    >
      {rangeErr && (
        <Alert color="red" role="alert" title="These dates cannot be used">
          {rangeErr} Pick the dates again above.
        </Alert>
      )}
      {query.error && (
        <Alert color="red" role="alert" title="The dashboard could not be loaded">
          {query.error instanceof ApiError
            ? [query.error.detail, ...Object.values(query.error.fieldErrors).flat()].join(' ')
            : query.error.message}
        </Alert>
      )}
      {data && (
        <Text size="sm">
          {formatCount(data.range.dates)} business date(s) with files or runs,{' '}
          {formatDate(data.range.date_from)} to {formatDate(data.range.date_to)}
          {chosen ? '.' : ' (the last 30; choose dates above for another range).'}
        </Text>
      )}

      {data && (
        <Stack gap="xs" component="section" aria-labelledby="dash-charts-title">
          <Title order={2} size="h4" id="dash-charts-title">
            Charts per business date
          </Title>
          <DashboardCharts data={data} />
        </Stack>
      )}

      {data && (
        <Stack gap="xs" component="section" aria-labelledby="dash-dates-title">
          <Title order={2} size="h4" id="dash-dates-title">
            Per business date
          </Title>
          <Table.ScrollContainer minWidth={1200}>
            <Table withTableBorder striped aria-label="Per business date">
              <Table.Thead>
                <Table.Tr>
                  {[
                    'Business date',
                    'Status',
                    'Transactions',
                    'NPCI',
                    'CBS',
                    'Exceptions',
                    'Open cases',
                    'Settlement',
                    'SSKUPI file',
                  ].map((h) => (
                    <Table.Th key={h} scope="col">
                      {h}
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.dates.map((d) => (
                  <Table.Tr key={d.business_date}>
                    <Table.Td>
                      <Anchor component={Link} to={spaLink(d.link) ?? `/${module}/dates/${d.business_date}`}>
                        {formatDate(d.business_date)}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      <DateStatusBadge status={d.status} label={d.status_label} />
                    </Table.Td>
                    <Table.Td ta="right">{formatCount(d.volume.transactions)}</Table.Td>
                    <Table.Td>
                      {formatCount(d.volume.npci.count)}
                      <Text size="xs" c="dimmed">
                        {formatRupees(d.volume.npci.amount)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      {formatCount(d.volume.cbs.count)}
                      <Text size="xs" c="dimmed">
                        {formatRupees(d.volume.cbs.amount)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      {formatCount(d.exceptions.total)}
                      <Text size="xs" c="dimmed">
                        {exceptionsLine(d, data.reply_types) || '-'}
                      </Text>
                    </Table.Td>
                    <Table.Td ta="right">{formatCount(d.open_cases)}</Table.Td>
                    <Table.Td>
                      <SettlementStatusBadge value={d.settlement?.status} />
                      {d.settlement && !d.settlement.tallied && (
                        <Text size="xs">Unexplained {formatRupees(d.settlement.unexplained_amount)}</Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {d.sskupi ? (
                        <>
                          <Text size="sm">
                            {d.sskupi.file_count === 0
                              ? 'No file'
                              : `${formatCount(d.sskupi.file_count)} file(s), ${d.sskupi.state.toLowerCase()}`}
                          </Text>
                          {d.sskupi.not_downloaded > 0 && (
                            <Text size="xs" c="orange">
                              {formatCount(d.sskupi.not_downloaded)} not downloaded
                            </Text>
                          )}
                          {d.sskupi.pending && d.sskupi.not_downloaded === 0 && (
                            <Text size="xs" c="orange">
                              Rows wait for a file
                            </Text>
                          )}
                        </>
                      ) : (
                        '-'
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
                {data.dates.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={9}>No business date with files or runs in this range.</Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Stack>
      )}

      {data && (
        <Stack gap="xs" component="section" aria-labelledby="dash-age-title">
          <Title order={2} size="h4" id="dash-age-title">
            Open cases by age (now)
          </Title>
          <Table withTableBorder aria-label="Open cases by age" maw={520}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Age</Table.Th>
                <Table.Th scope="col" ta="right">
                  Open
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Overdue
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.open_cases_by_age.buckets.map((b) => (
                <Table.Tr key={b.label}>
                  <Table.Td>{b.label}</Table.Td>
                  <Table.Td ta="right">{formatCount(b.count)}</Table.Td>
                  <Table.Td ta="right">{formatCount(b.overdue)}</Table.Td>
                </Table.Tr>
              ))}
              <Table.Tr>
                <Table.Th scope="row">Total</Table.Th>
                <Table.Td ta="right" fw={700}>
                  {formatCount(data.open_cases_by_age.total)}
                </Table.Td>
                <Table.Td ta="right" fw={700}>
                  {formatCount(data.open_cases_by_age.overdue)}
                </Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        </Stack>
      )}
    </ReportFrame>
  );
}
