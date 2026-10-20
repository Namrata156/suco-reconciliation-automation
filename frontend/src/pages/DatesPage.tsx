/**
 * Business-date overview list (design 4.11): one row per date with the file
 * checklist counts, matrix, rule evaluation, cases, settlement and the last
 * run. Paged on the server; refreshes itself while a run is active or files
 * are being processed on a shown date (lib/polling.ts).
 */
import { Alert, Anchor, Button, Group, Pagination, Select, Stack, Table, Text, Title } from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router';

import { listDates } from '../api/client';
import type { BusinessDateOverview, DateFilters, Paginated } from '../api/types';
import { AutoRefresh } from '../components/AutoRefresh';
import { RunStatusBadge, SettlementStatusBadge } from '../components/Badges';
import { isRunActive } from '../features/dates/RunPanel';
import { useModuleCode } from '../hooks/module';
import { formatCount, formatDate, formatDateTime } from '../lib/format';
import { pollInterval, pollingState, useUserActive } from '../lib/polling';

export const PAGE_SIZE = 50;

/** Something on this date is still changing on the server. */
export const dateIsBusy = (o: BusinessDateOverview) => isRunActive(o.last_run) || o.files.files_processing > 0;

export function dateFiltersFromParams(params: URLSearchParams): DateFilters {
  const f: DateFilters = {};
  for (const k of ['date_from', 'date_to', 'ordering'] as const) {
    const v = params.get(k);
    if (v) f[k] = v;
  }
  const page = Number(params.get('page') || '1');
  f.page = Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
  f.page_size = PAGE_SIZE;
  return f;
}

export function DatesPage() {
  const module = useModuleCode();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => dateFiltersFromParams(params), [params]);
  const setFilter = (key: 'date_from' | 'date_to' | 'ordering' | 'page', value: string | null) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== 'page') next.delete('page');
      return next;
    });
  };

  const active = useUserActive();
  const busy = (p?: Paginated<BusinessDateOverview>) => (p?.results ?? []).some(dateIsBusy);
  const query = useQuery({
    queryKey: ['dates', module, filters],
    queryFn: () => listDates(module, filters),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => pollInterval(busy(q.state.data), active),
  });
  const polling = pollingState(busy(query.data), active);

  const rows = query.data?.results ?? [];
  const count = query.data?.count ?? 0;
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const page = filters.page ?? 1;

  return (
    <Stack gap="sm">
      <Title order={1} size="h3">
        Business dates
      </Title>
      <Group gap="xs" align="flex-end" wrap="wrap" component="section" aria-label="Filters">
        <DateInput
          label="From"
          valueFormat="DD-MMM-YYYY"
          value={filters.date_from ?? null}
          onChange={(v) => setFilter('date_from', v)}
          clearable
          w={150}
        />
        <DateInput
          label="To"
          valueFormat="DD-MMM-YYYY"
          value={filters.date_to ?? null}
          onChange={(v) => setFilter('date_to', v)}
          clearable
          w={150}
        />
        <Select
          label="Sort"
          data={[
            { value: '', label: 'Newest first (default)' },
            { value: 'business_date', label: 'Oldest first' },
          ]}
          value={filters.ordering ?? ''}
          onChange={(v) => setFilter('ordering', v)}
          w={200}
          allowDeselect={false}
        />
        <Button variant="subtle" onClick={() => setParams(new URLSearchParams())}>
          Reset filters
        </Button>
      </Group>

      <AutoRefresh
        polling={polling}
        what="a run or file processing is in progress"
        onRefresh={() => void query.refetch()}
        busy={query.isFetching}
      />

      {query.error && (
        <Alert color="red" role="alert" title="The business dates could not be loaded">
          {query.error.message}
        </Alert>
      )}
      <Text size="sm" aria-live="polite">
        {query.isPending ? 'Loading...' : `${formatCount(count)} business date(s)`}
      </Text>

      <Table.ScrollContainer minWidth={1100}>
        <Table aria-label="Business dates" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Business date</Table.Th>
              <Table.Th scope="col">Expected files present</Table.Th>
              <Table.Th scope="col">Missing</Table.Th>
              <Table.Th scope="col">Awaiting date / failed / processing</Table.Th>
              <Table.Th scope="col">Matrix</Table.Th>
              <Table.Th scope="col">Rule evaluation</Table.Th>
              <Table.Th scope="col">Cases (active)</Table.Th>
              <Table.Th scope="col">Settlement</Table.Th>
              <Table.Th scope="col">Last run</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((o) => (
              <Table.Tr key={o.business_date}>
                <Table.Td>
                  <Anchor component={Link} to={`/${module}/dates/${o.business_date}`}>
                    {formatDate(o.business_date)}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  {o.files.expected - o.files.missing} of {o.files.expected} ({o.files.loaded} loaded)
                </Table.Td>
                <Table.Td>{o.files.missing}</Table.Td>
                <Table.Td>
                  {o.files.awaiting_date_selection} / {o.files.failed} / {o.files.processing}
                </Table.Td>
                <Table.Td>{o.matrix.built ? `built (${formatCount(o.matrix.match_groups)} groups)` : 'not built'}</Table.Td>
                <Table.Td>
                  {o.rule_evaluation
                    ? `${o.rule_evaluation.rule_set}, ${formatDateTime(o.rule_evaluation.finished_at ?? o.rule_evaluation.started_at)}`
                    : 'not run'}
                </Table.Td>
                <Table.Td>
                  {formatCount(o.cases.total)} ({formatCount(o.cases.active)})
                </Table.Td>
                <Table.Td>
                  <SettlementStatusBadge value={o.settlement?.status} />
                </Table.Td>
                <Table.Td>{o.last_run ? <RunStatusBadge value={o.last_run.status} /> : 'none'}</Table.Td>
              </Table.Tr>
            ))}
            {!query.isPending && rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={9}>No business dates yet: upload a day&apos;s files first.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      {pages > 1 && (
        <Pagination
          total={pages}
          value={page}
          onChange={(p) => setFilter('page', String(p))}
          getControlProps={(control) => ({ 'aria-label': `${control} page` })}
          getItemProps={(p) => ({ 'aria-label': `Page ${p}` })}
        />
      )}
    </Stack>
  );
}
