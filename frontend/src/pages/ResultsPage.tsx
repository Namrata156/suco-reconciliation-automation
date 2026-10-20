/**
 * Reconciliation results per date (design 4.12, LARGE): about 70,000 match
 * groups a date, so only one page of 50 is ever loaded. Filters live in the
 * address, so the summary's per-rule links open a filtered list.
 */
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router';

import { ApiError, listResults } from '../api/client';
import type { ModuleMeta, PresenceLeg, ResultFilters } from '../api/types';
import { PriorityBadge, StatusBadge } from '../components/Badges';
import { ListPager } from '../components/ListPager';
import { labelOf, useMeta, useModuleCode } from '../hooks/module';
import { formatAmount, formatCount, formatDate } from '../lib/format';
import { pageBounds, useUrlFilters } from '../lib/urlFilters';

export const RESULTS_PAGE_SIZE = 50;

export const RESULT_FILTER_KEYS = [
  'rule_code',
  'status_category',
  'priority',
  'processing_status',
  'presence',
  'rrn',
  'on_us',
  'reversed',
  'superseded',
  'evaluated',
  'ordering',
] as const;

const BOOL = (yes: string, no: string) => [
  { value: '', label: 'Any' },
  { value: 'true', label: yes },
  { value: 'false', label: no },
];

/** meta.result_orderings as sort choices, each both ways. */
export function resultOrderings(fields: readonly string[]): { value: string; label: string }[] {
  const name = (f: string) => f.replace(/_/g, ' ');
  return [
    { value: '', label: 'RRN (default)' },
    ...fields.flatMap((f) => [
      { value: f, label: f === 'priority' ? 'Priority (HIGH first)' : `${name(f)} (ascending)` },
      { value: `-${f}`, label: f === 'priority' ? 'Priority (LOW first)' : `${name(f)} (descending)` },
    ]),
  ].filter((o) => o.value !== 'rrn');
}

function LegCell({ leg }: { leg: PresenceLeg }) {
  if (!leg.present) return <Text size="sm">absent</Text>;
  return (
    <>
      <Text size="sm">{formatAmount(leg.amount)}</Text>
      <Text size="xs" c="dimmed">
        {[leg.direction, leg.response_code ? `RC ${leg.response_code}` : ''].filter(Boolean).join(', ') ||
          '-'}
      </Text>
    </>
  );
}

const opt = (list: { value: string; label: string }[]) => [{ value: '', label: 'Any' }, ...list];

function ruleChoices(meta: ModuleMeta | undefined) {
  return opt((meta?.rules ?? []).map((r) => ({ value: r.code, label: `${r.code} - ${r.name}` })));
}

export function ResultsPage() {
  const module = useModuleCode();
  const businessDate = useParams().businessDate ?? '';
  const { data: meta } = useMeta(module);
  const { filters, setFilter, reset } = useUrlFilters(RESULT_FILTER_KEYS, RESULTS_PAGE_SIZE);
  const [rrn, setRrn] = useState(filters.rrn ?? '');
  const valid = /^\d{4}-\d{2}-\d{2}$/.test(businessDate);

  const query = useQuery({
    queryKey: ['results', module, businessDate, filters],
    queryFn: () => listResults(module, businessDate, filters as ResultFilters),
    placeholderData: keepPreviousData,
    enabled: valid,
  });

  if (!valid) return <Alert color="red">Invalid date address.</Alert>;

  const rows = query.data?.results ?? [];
  const count = query.data?.count ?? 0;
  const page = filters.page;
  const { pages, first, last } = pageBounds(count, page, RESULTS_PAGE_SIZE);
  const notFound = query.error instanceof ApiError && query.error.status === 404;
  const searchRrn = (e: FormEvent) => {
    e.preventDefault();
    setFilter('rrn', rrn.trim());
  };
  const select = (
    key: (typeof RESULT_FILTER_KEYS)[number],
    label: string,
    data: { value: string; label: string }[],
    w = 180,
  ) => (
    <Select
      label={label}
      data={data}
      value={filters[key] ?? ''}
      onChange={(v) => setFilter(key, v)}
      w={w}
      allowDeselect={false}
      searchable={data.length > 12}
    />
  );

  return (
    <Stack gap="sm">
      <Anchor component={Link} to={`/${module}/dates/${businessDate}`} size="sm">
        Back to the business date
      </Anchor>
      <Title order={1} size="h3">
        Reconciliation results {formatDate(businessDate)}
      </Title>

      <Group gap="xs" align="flex-end" wrap="wrap" component="section" aria-label="Filters">
        {select('rule_code', 'Rule', ruleChoices(meta), 220)}
        {select('status_category', 'Status category', opt(meta?.status_categories ?? []), 220)}
        {select('priority', 'Priority', opt(meta?.priorities ?? []), 120)}
        {select('presence', 'Presence', opt(meta?.presence_patterns ?? []), 220)}
        {select('processing_status', 'Match status', opt(meta?.match_group_statuses ?? []), 200)}
        {select('on_us', 'On-us', BOOL('On-us only', 'Not on-us'), 130)}
        {select('reversed', 'CBS reversed', BOOL('Reversed only', 'Not reversed'), 140)}
        {select('superseded', 'Superseded', BOOL('Superseded only', 'Current only (default)'), 190)}
        {select('ordering', 'Sort', resultOrderings(meta?.result_orderings ?? []), 220)}
        <form onSubmit={searchRrn}>
          <Group gap={4} align="flex-end">
            <TextInput label="RRN" value={rrn} onChange={(e) => setRrn(e.currentTarget.value)} w={150} />
            <Button type="submit" variant="default">
              Find RRN
            </Button>
          </Group>
        </form>
        <Button
          variant="subtle"
          onClick={() => {
            setRrn('');
            reset();
          }}
        >
          Reset filters
        </Button>
      </Group>

      {query.error && (
        <Alert
          color={notFound ? 'gray' : 'red'}
          role="alert"
          title={notFound ? 'No results' : 'The results could not be loaded'}
        >
          {notFound ? `No reconciliation results for ${formatDate(businessDate)} yet.` : query.error.message}
        </Alert>
      )}
      <Text size="sm" aria-live="polite">
        {query.isPending
          ? 'Loading results...'
          : `Showing ${formatCount(first)}-${formatCount(last)} of ${formatCount(count)} match groups`}
      </Text>

      <Table.ScrollContainer minWidth={1200}>
        <Table aria-label="Reconciliation results" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">RRN</Table.Th>
              <Table.Th scope="col">Cycle</Table.Th>
              <Table.Th scope="col">CBS</Table.Th>
              <Table.Th scope="col">NPCI</Table.Th>
              <Table.Th scope="col">Overlay rows</Table.Th>
              <Table.Th scope="col">Rule</Table.Th>
              <Table.Th scope="col">Status category</Table.Th>
              <Table.Th scope="col">Priority</Table.Th>
              <Table.Th scope="col">Match status</Table.Th>
              <Table.Th scope="col">Flags</Table.Th>
              <Table.Th scope="col">Case</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((r) => (
              <Table.Tr key={r.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/${module}/dates/${businessDate}/groups/${r.id}`}>
                    {r.rrn}
                  </Anchor>
                </Table.Td>
                <Table.Td>{r.settlement_cycle ?? '-'}</Table.Td>
                <Table.Td>
                  <LegCell leg={r.cbs} />
                </Table.Td>
                <Table.Td>
                  <LegCell leg={r.npci} />
                </Table.Td>
                <Table.Td>{r.overlays.length ? r.overlays.join(', ') : '-'}</Table.Td>
                <Table.Td>{r.rule_result?.rule_code ?? 'not evaluated'}</Table.Td>
                <Table.Td>{labelOf(meta?.status_categories, r.rule_result?.status_category)}</Table.Td>
                <Table.Td>
                  {r.rule_result ? (
                    <PriorityBadge value={r.rule_result.priority} choices={meta?.priorities} />
                  ) : (
                    '-'
                  )}
                </Table.Td>
                <Table.Td>{labelOf(meta?.match_group_statuses, r.processing_status)}</Table.Td>
                <Table.Td>
                  <Group gap={4}>
                    {r.is_on_us && (
                      <Badge size="sm" variant="outline">
                        On-us
                      </Badge>
                    )}
                    {r.cbs_reversed && (
                      <Badge size="sm" variant="outline" color="orange">
                        CBS reversed
                      </Badge>
                    )}
                    {r.amount_match === false && (
                      <Badge size="sm" variant="outline" color="red">
                        Amount mismatch
                      </Badge>
                    )}
                    {r.is_superseded && (
                      <Badge size="sm" variant="outline" color="gray">
                        Superseded
                      </Badge>
                    )}
                  </Group>
                </Table.Td>
                <Table.Td>
                  {r.case ? (
                    <>
                      <Anchor
                        component={Link}
                        to={`/${module}/cases/${r.case.id}`}
                        aria-label={`Case for ${r.rrn}`}
                      >
                        #{r.case.id}
                      </Anchor>{' '}
                      <StatusBadge value={r.case.status} choices={meta?.case_statuses} />
                    </>
                  ) : (
                    '-'
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            {!query.isPending && !query.error && rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={11}>No match groups match these filters.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>

      <ListPager total={pages} value={page} onChange={(p) => setFilter('page', String(p))} />
    </Stack>
  );
}
