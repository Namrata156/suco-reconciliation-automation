/**
 * Exception case queue (design 4.4). Paged on the server (50 per page); the
 * filters live in the address, so home-page counts, bookmarks and alert links
 * open the same list.
 *
 * UI refinements U2 (6.1 / 6.2; contract A3): tabs by NPCI reply type with
 * their counts (DRC | TCC | RET | RRC | Needs review | Settlement; one
 * "Needs review" tab, owner Q1); only Whose cases, Business date and RRN are
 * in view, the other filters under "More filters" (open by itself while one
 * of them is set). Columns add the amount and the NPCI reply, and show the
 * plain rule name. "Awaiting my approval" is the Approvals inbox now
 * (7.1): ?mine=approval opens it.
 *
 * Phase 6 (contract C2): the ageing report's cells open this list with
 * ageing_bucket (and a rule_code that may be a comma list); both are
 * filters here too.
 */
import {
  Alert,
  Anchor,
  Box,
  Button,
  Checkbox,
  Group,
  Pagination,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useDebouncedValue } from '@mantine/hooks';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type RowSelectionState,
} from '@tanstack/react-table';
import { useEffect, useMemo, useState } from 'react';
import { Link, Navigate, useSearchParams } from 'react-router';

import { listCases } from '../api/client';
import type { CaseFilters, CaseListItem, ModuleMeta, ReplyType } from '../api/types';
import { useMe } from '../auth/auth';
import { ClosedBySystemBadge, FlagBadges, StatusBadge } from '../components/Badges';
import { BulkBar } from '../features/cases/BulkBar';
import { useMeta, useModuleCode } from '../hooks/module';
import { formatCount, formatDate, formatRupees } from '../lib/format';

export const PAGE_SIZE = 50;
const FILTER_KEYS = [
  'business_date',
  'status',
  'rule_code',
  'case_type',
  'overdue',
  'mine',
  'flag',
  'rrn',
  'due_date',
  'ordering',
  'reply_type',
  'ageing_bucket',
] as const;
type FilterKey = (typeof FILTER_KEYS)[number];

/** The queue tabs (contract A3 "Reply types"), in this order. */
export const REPLY_TABS: { value: ReplyType; label: string }[] = [
  { value: 'DRC', label: 'DRC' },
  { value: 'TCC', label: 'TCC' },
  { value: 'RET', label: 'RET' },
  { value: 'RRC', label: 'RRC' },
  { value: 'NEEDS_REVIEW', label: 'Needs review' },
  { value: 'SETTLEMENT', label: 'Settlement' },
];

/** The filters under "More filters"; set ones open it. */
export const MORE_FILTER_KEYS = [
  'due_date',
  'status',
  'rule_code',
  'case_type',
  'overdue',
  'flag',
  'ordering',
  'ageing_bucket',
] as const satisfies readonly FilterKey[];

export function filtersFromParams(params: URLSearchParams): CaseFilters {
  const f: CaseFilters = {};
  for (const k of FILTER_KEYS) {
    const v = params.get(k);
    if (v) (f as Record<string, string>)[k] = v;
  }
  const page = Number(params.get('page') || '1');
  f.page = Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
  f.page_size = PAGE_SIZE;
  return f;
}

const ORDERING_LABELS: Record<string, [string, string]> = {
  business_date: ['Business date (oldest first)', 'Business date (newest first)'],
  due_date: ['Due date (earliest first)', 'Due date (latest first)'],
  age: ['Age (youngest first)', 'Age (oldest first)'],
  status: ['Status (A-Z)', 'Status (Z-A)'],
  rule_code: ['Rule (A-Z)', 'Rule (Z-A)'],
  rrn: ['RRN (ascending)', 'RRN (descending)'],
  created_at: ['Created (oldest first)', 'Created (newest first)'],
  updated_at: ['Updated (oldest first)', 'Updated (newest first)'],
};

/**
 * Sort choices from meta.case_orderings (field names; "-" reverses). The
 * case priority is not shown anywhere, so it is not offered as a sort.
 */
export function orderingChoices(fields: readonly string[] | undefined) {
  return (fields ?? []).filter((f) => f !== 'priority').flatMap((f) => {
    const [asc, desc] = ORDERING_LABELS[f] ?? [`${f} (ascending)`, `${f} (descending)`];
    return [
      { value: f, label: asc },
      { value: `-${f}`, label: desc },
    ];
  });
}

function caseLabel(c: CaseListItem) {
  return c.case_type === 'SETTLEMENT' ? `Settlement (NTSL) ${formatDate(c.business_date)}` : c.rrn;
}

function columns(module: string, meta: ModuleMeta | undefined): ColumnDef<CaseListItem>[] {
  return [
    {
      id: 'select',
      header: ({ table }) => (
        <Checkbox
          aria-label="Select all cases on this page"
          checked={table.getIsAllRowsSelected()}
          indeterminate={table.getIsSomeRowsSelected()}
          onChange={table.getToggleAllRowsSelectedHandler()}
        />
      ),
      cell: ({ row }) => (
        <Checkbox
          aria-label={`Select case ${caseLabel(row.original)}`}
          checked={row.getIsSelected()}
          onChange={row.getToggleSelectedHandler()}
        />
      ),
    },
    {
      id: 'case',
      header: 'Case (RRN)',
      cell: ({ row }) => (
        <Anchor component={Link} to={`/${module}/cases/${row.original.id}`} className="wrap-anywhere">
          {caseLabel(row.original)}
        </Anchor>
      ),
    },
    {
      id: 'business_date',
      header: 'Business date',
      cell: ({ row }) => formatDate(row.original.business_date),
    },
    {
      id: 'amount',
      header: 'Amount',
      cell: ({ row }) => (row.original.amount ? formatRupees(row.original.amount) : '-'),
    },
    {
      id: 'reply',
      header: 'NPCI reply',
      cell: ({ row }) => {
        const r = row.original.reply;
        if (!r?.label) return '-';
        return (
          <>
            {r.label}
            <Text size="xs" c="dimmed">
              {r.source_label}
            </Text>
          </>
        );
      },
    },
    {
      id: 'rule',
      header: 'Rule',
      cell: ({ row }) => {
        const c = row.original;
        if (!c.rule_name) return c.rule_code || '-';
        return (
          <>
            {c.rule_name}{' '}
            <Text size="xs" c="dimmed" span>
              {c.rule_code}
            </Text>
          </>
        );
      },
    },
    {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => (
        <Group gap={4} wrap="wrap">
          <StatusBadge value={row.original.status} choices={meta?.case_statuses} />
          {row.original.closed_by_system && <ClosedBySystemBadge fileName={row.original.npci_file?.file_name} />}
        </Group>
      ),
    },
    { id: 'flags', header: 'Flags', cell: ({ row }) => <FlagBadges flags={row.original.flags} /> },
    {
      id: 'age',
      header: 'Age (days)',
      cell: ({ row }) => (
        <>
          {row.original.age_days ?? '-'}
          {row.original.ageing_bucket && (
            <Text size="xs" c="dimmed">
              {row.original.ageing_bucket}
            </Text>
          )}
        </>
      ),
    },
    {
      id: 'due',
      header: 'Due',
      cell: ({ row }) =>
        row.original.due_date
          ? `${formatDate(row.original.due_date)}${row.original.due_cutoff_time ? ` ${row.original.due_cutoff_time.slice(0, 5)}` : ''}`
          : '-',
    },
    { id: 'holder', header: 'Held by', cell: ({ row }) => row.original.assigned_to ?? '-' },
  ];
}

export function CaseQueuePage() {
  const me = useMe();
  const module = useModuleCode();
  const { data: meta } = useMeta(module);
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => filtersFromParams(params), [params]);

  // RRN search: sent after a short pause in typing (design 7.6).
  const [rrnInput, setRrnInput] = useState(filters.rrn ?? '');
  const [rrn] = useDebouncedValue(rrnInput.trim(), 400);

  const setFilter = (key: FilterKey | 'page', value: string | null) => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        if (key !== 'page') next.delete('page');
        return next;
      },
      { replace: key === 'rrn' },
    );
  };

  useEffect(() => {
    if ((filters.rrn ?? '') !== rrn) setFilter('rrn', rrn || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the debounced value settles
  }, [rrn]);
  // The address changed from outside (menu link, "Reset filters", back button).
  useEffect(() => setRrnInput(filters.rrn ?? ''), [filters.rrn]);

  const query = useQuery({
    queryKey: ['cases', module, filters],
    queryFn: () => listCases(module, filters),
    placeholderData: keepPreviousData,
  });

  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  // A selection belongs to one page and one set of filters.
  useEffect(() => setRowSelection({}), [filters, module]);

  const rows = useMemo(() => query.data?.results ?? [], [query.data]);
  const cols = useMemo(() => columns(module, meta), [module, meta]);
  const table = useReactTable({
    data: rows,
    columns: cols,
    getRowId: (r) => String(r.id),
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualFiltering: true,
    manualSorting: true,
    rowCount: query.data?.count ?? 0,
    state: { rowSelection },
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
  });
  const selected = table.getSelectedRowModel().rows.map((r) => r.original);

  const count = query.data?.count ?? 0;
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const page = filters.page ?? 1;
  const first = count === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(count, page * PAGE_SIZE);

  const statusData = [
    { value: '', label: 'Active (default)' },
    { value: 'all', label: 'All statuses' },
    ...(meta?.case_statuses ?? []),
  ];
  // A comma list from a home-page link ("OPEN,IN_PROGRESS") is shown as one choice.
  if (filters.status && !statusData.some((s) => s.value === filters.status)) {
    statusData.push({
      value: filters.status,
      label: filters.status
        .split(',')
        .map((s) => meta?.case_statuses.find((c) => c.value === s)?.label ?? s)
        .join(' / '),
    });
  }
  const opt = (list: { value: string; label: string }[] | undefined) => [
    { value: '', label: 'Any' },
    ...(list ?? []),
  ];
  // A value from a link that is not one choice (a comma list of rules or buckets) stays visible.
  const keep = (list: { value: string; label: string }[], value: string | undefined) =>
    value && !list.some((o) => o.value === value)
      ? [...list, { value, label: value.split(',').join(' / ') }]
      : list;
  const tabCounts = query.data?.reply_type_counts;
  const tabCount = (v: ReplyType) => (tabCounts ? ` (${formatCount(tabCounts[v] ?? 0)})` : '');
  const allCount = tabCounts ? Object.values(tabCounts).reduce((t, n) => t + (n ?? 0), 0) : undefined;
  const moreSet = MORE_FILTER_KEYS.filter((k) => filters[k]).length;

  // "Awaiting my approval" is the Approvals inbox (7.1); old links and alerts still work.
  if (filters.mine === 'approval') return <Navigate to={`/${module}/approvals`} replace />;

  return (
    <Stack gap="sm">
      <Title order={1} size="h3">
        Exception cases
      </Title>

      <Tabs
        value={filters.reply_type ?? 'ALL'}
        onChange={(v) => setFilter('reply_type', v === 'ALL' ? null : v)}
        keepMounted={false}
      >
        <Tabs.List aria-label="Cases by NPCI reply">
          <Tabs.Tab value="ALL">All{allCount === undefined ? '' : ` (${formatCount(allCount)})`}</Tabs.Tab>
          {REPLY_TABS.map((t) => (
            <Tabs.Tab key={t.value} value={t.value}>
              {t.label}
              {tabCount(t.value)}
            </Tabs.Tab>
          ))}
          {/* A reply_type from a link that is not one tab (a csv) stays visible. */}
          {filters.reply_type && !REPLY_TABS.some((t) => t.value === filters.reply_type) && (
            <Tabs.Tab value={filters.reply_type}>{filters.reply_type}</Tabs.Tab>
          )}
        </Tabs.List>
      </Tabs>

      <Box component="section" aria-label="Filters">
        <Group gap="xs" align="flex-end" wrap="wrap">
          <SegmentedControl
            aria-label="Whose cases"
            value={filters.mine ?? ''}
            onChange={(v) => setFilter('mine', v)}
            data={[
              { value: '', label: 'All cases' },
              { value: 'assigned', label: 'Assigned to me' },
            ]}
          />
          <DateInput
            label="Business date"
            valueFormat="DD-MMM-YYYY"
            value={filters.business_date ?? null}
            onChange={(v) => setFilter('business_date', v)}
            clearable
            w={150}
          />
          <TextInput
            label="RRN (exact)"
            value={rrnInput}
            onChange={(e) => setRrnInput(e.currentTarget.value)}
            w={160}
          />
          <Button
            variant="subtle"
            onClick={() => {
              setRrnInput('');
              setParams(new URLSearchParams());
            }}
          >
            Reset filters
          </Button>
        </Group>
        <details className="details-section" open={moreSet > 0 || undefined}>
          <summary>
            <Text size="sm" fw={600} span>
              More filters{moreSet ? ` (${moreSet} set)` : ''}
            </Text>
          </summary>
          <Group gap="xs" align="flex-end" wrap="wrap" mt="xs">
            <DateInput
              label="Due date"
              valueFormat="DD-MMM-YYYY"
              value={filters.due_date ?? null}
              onChange={(v) => setFilter('due_date', v)}
              clearable
              w={150}
            />
            <Select
              label="Status"
              data={statusData}
              value={filters.status ?? ''}
              onChange={(v) => setFilter('status', v)}
              w={170}
              allowDeselect={false}
            />
            <Select
              label="Rule"
              data={keep(
                opt(meta?.rules.map((r) => ({ value: r.code, label: r.code }))),
                filters.rule_code,
              )}
              value={filters.rule_code ?? ''}
              onChange={(v) => setFilter('rule_code', v)}
              w={110}
              searchable
              allowDeselect={false}
            />
            <Select
              label="Age"
              data={keep(
                opt(meta?.ageing_buckets.map((b) => ({ value: b.label, label: b.label }))),
                filters.ageing_bucket,
              )}
              value={filters.ageing_bucket ?? ''}
              onChange={(v) => setFilter('ageing_bucket', v)}
              w={120}
              allowDeselect={false}
            />
            <Select
              label="Case type"
              data={opt(meta?.case_types)}
              value={filters.case_type ?? ''}
              onChange={(v) => setFilter('case_type', v)}
              w={150}
              allowDeselect={false}
            />
            <Select
              label="Overdue"
              data={[
                { value: '', label: 'Any' },
                { value: 'true', label: 'Overdue' },
                { value: 'false', label: 'Not overdue' },
              ]}
              value={filters.overdue ?? ''}
              onChange={(v) => setFilter('overdue', v)}
              w={120}
              allowDeselect={false}
            />
            <Select
              label="Flag"
              data={opt(meta?.flags)}
              value={filters.flag ?? ''}
              onChange={(v) => setFilter('flag', v)}
              w={170}
              allowDeselect={false}
            />
            <Select
              label="Sort"
              data={[
                { value: '', label: 'Queue order (default)' },
                ...orderingChoices(meta?.case_orderings),
                // Keep a value from a link visible even if it is not a single field.
                ...(filters.ordering &&
                !orderingChoices(meta?.case_orderings).some((o) => o.value === filters.ordering)
                  ? [{ value: filters.ordering, label: filters.ordering }]
                  : []),
              ]}
              value={filters.ordering ?? ''}
              onChange={(v) => setFilter('ordering', v)}
              w={200}
              allowDeselect={false}
            />
          </Group>
        </details>
      </Box>

      {query.error && (
        <Alert color="red" role="alert" title="The cases could not be loaded">
          {query.error.message}
        </Alert>
      )}

      <Text size="sm" aria-live="polite">
        {query.isPending
          ? 'Loading cases...'
          : `Showing ${formatCount(first)}-${formatCount(last)} of ${formatCount(count)} cases`}
      </Text>

      <Table.ScrollContainer minWidth={1100}>
        <Table aria-busy={query.isFetching} aria-label="Exception cases">
          <Table.Thead>
            {table.getHeaderGroups().map((hg) => (
              <Table.Tr key={hg.id}>
                {hg.headers.map((h) => (
                  <Table.Th key={h.id} scope="col">
                    {h.isPlaceholder ? null : flexRender(h.column.columnDef.header, h.getContext())}
                  </Table.Th>
                ))}
              </Table.Tr>
            ))}
          </Table.Thead>
          <Table.Tbody>
            {table.getRowModel().rows.map((row) => (
              <Table.Tr key={row.id} aria-selected={row.getIsSelected()}>
                {row.getVisibleCells().map((cell) => (
                  <Table.Td key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </Table.Td>
                ))}
              </Table.Tr>
            ))}
            {!query.isPending && rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={cols.length}>No cases match these filters.</Table.Td>
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

      <BulkBar
        module={module}
        selected={selected}
        permissions={me.permissions}
        username={me.username}
        meta={meta}
        onDone={() => setRowSelection({})}
        onClear={() => setRowSelection({})}
      />
    </Stack>
  );
}
