/**
 * Exception case queue (design 4.4). Paged on the server (50 per page); the
 * filters live in the address, so home-page counts, bookmarks and alert links
 * open the same list.
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
import { Link, useSearchParams } from 'react-router';

import { listCases } from '../api/client';
import type { CaseFilters, CaseListItem, ModuleMeta } from '../api/types';
import { useMe } from '../auth/auth';
import { FlagBadges, PriorityBadge, StatusBadge } from '../components/Badges';
import { BulkBar } from '../features/cases/BulkBar';
import { useMeta, useModuleCode } from '../hooks/module';
import { formatCount, formatDate } from '../lib/format';
import { can } from '../lib/roles';

export const PAGE_SIZE = 50;
const FILTER_KEYS = [
  'business_date',
  'status',
  'priority',
  'rule_code',
  'case_type',
  'overdue',
  'mine',
  'flag',
  'rrn',
  'due_date',
  'ordering',
] as const;
type FilterKey = (typeof FILTER_KEYS)[number];

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
  priority: ['Priority (HIGH first)', 'Priority (LOW first)'],
  business_date: ['Business date (oldest first)', 'Business date (newest first)'],
  due_date: ['Due date (earliest first)', 'Due date (latest first)'],
  age: ['Age (youngest first)', 'Age (oldest first)'],
  status: ['Status (A-Z)', 'Status (Z-A)'],
  rule_code: ['Rule (A-Z)', 'Rule (Z-A)'],
  rrn: ['RRN (ascending)', 'RRN (descending)'],
  created_at: ['Created (oldest first)', 'Created (newest first)'],
  updated_at: ['Updated (oldest first)', 'Updated (newest first)'],
};

/** Sort choices from meta.case_orderings (field names; "-" reverses). */
export function orderingChoices(fields: readonly string[] | undefined) {
  return (fields ?? []).flatMap((f) => {
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
    { id: 'rule', header: 'Rule', cell: ({ row }) => row.original.rule_code || '-' },
    {
      id: 'priority',
      header: 'Priority',
      cell: ({ row }) => <PriorityBadge value={row.original.priority} choices={meta?.priorities} />,
    },
    {
      id: 'status',
      header: 'Status',
      cell: ({ row }) => <StatusBadge value={row.original.status} choices={meta?.case_statuses} />,
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

  const checker = can(me.permissions, 'cases.approve');
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

  return (
    <Stack gap="sm">
      <Title order={1} size="h3">
        Exception cases
      </Title>

      <Box component="section" aria-label="Filters">
        <Group gap="xs" align="flex-end" wrap="wrap">
          <SegmentedControl
            aria-label="Whose cases"
            value={filters.mine ?? ''}
            onChange={(v) => setFilter('mine', v)}
            data={[
              { value: '', label: 'All cases' },
              { value: 'assigned', label: 'Assigned to me' },
              ...(checker ? [{ value: 'approval', label: 'Awaiting my approval' }] : []),
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
            label="Priority"
            data={opt(meta?.priorities)}
            value={filters.priority ?? ''}
            onChange={(v) => setFilter('priority', v)}
            w={110}
            allowDeselect={false}
          />
          <Select
            label="Rule"
            data={opt(meta?.rules.map((r) => ({ value: r.code, label: r.code })))}
            value={filters.rule_code ?? ''}
            onChange={(v) => setFilter('rule_code', v)}
            w={110}
            searchable
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
          <TextInput
            label="RRN (exact)"
            value={rrnInput}
            onChange={(e) => setRrnInput(e.currentTarget.value)}
            w={160}
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
