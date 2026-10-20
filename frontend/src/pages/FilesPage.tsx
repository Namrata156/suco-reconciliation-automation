/**
 * Files and processing status (design 4.9). Paged on the server (50 per
 * page); the filters live in the address, as in the case queue. The list
 * refreshes itself while any file of the module is still being processed
 * (processing_count), within the idle-timeout rule of lib/polling.ts. The
 * status and file-type choices come from /meta/ (Slice 3).
 */
import { Alert, Anchor, Button, Group, Pagination, Select, Stack, Table, Text, Title } from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { Link, useSearchParams } from 'react-router';

import { listFiles } from '../api/client';
import type { FileFilters, FileListPage, ModuleMeta, UploadedFileRow } from '../api/types';
import { useMe } from '../auth/auth';
import { AutoRefresh } from '../components/AutoRefresh';
import { FileStateBadge } from '../components/Badges';
import { useMeta, useModuleCode } from '../hooks/module';
import { formatCount, formatDate, formatDateTime } from '../lib/format';
import { pollInterval, pollingState, useUserActive } from '../lib/polling';
import { can } from '../lib/roles';

export const PAGE_SIZE = 50;

const ORDERINGS = [
  { value: '', label: 'Newest upload first (default)' },
  { value: 'uploaded_at', label: 'Oldest upload first' },
  { value: '-business_date', label: 'Business date (newest first)' },
  { value: 'business_date', label: 'Business date (oldest first)' },
  { value: 'filename', label: 'File name (A-Z)' },
  { value: 'file_type', label: 'File type (A-Z)' },
  { value: 'processing_status', label: 'Status (A-Z)' },
];

// file_type (a type code, both sides) still works in an address; the screen
// filters on file_type_config (one side: Raw Data ACQ vs ISS; meta, Slice 3).
const FILTER_KEYS = [
  'business_date',
  'file_type',
  'file_type_config',
  'cycle',
  'status',
  'current',
  'ordering',
] as const;
type FilterKey = (typeof FILTER_KEYS)[number];

export function fileFiltersFromParams(params: URLSearchParams): FileFilters {
  const f: FileFilters = {};
  for (const k of FILTER_KEYS) {
    const v = params.get(k);
    if (v) (f as Record<string, string>)[k] = v;
  }
  const page = Number(params.get('page') || '1');
  f.page = Number.isFinite(page) && page > 0 ? Math.floor(page) : 1;
  f.page_size = PAGE_SIZE;
  return f;
}

/** meta.processing_statuses (pipeline order) with the short labels. */
export function fileStatusChoices(meta: ModuleMeta | undefined): { value: string; label: string }[] {
  return (meta?.processing_statuses ?? []).map((s) => ({ value: s.value, label: shortStatus(s.label) }));
}

/** meta.file_type_configs: "Raw Data ACQ", "Raw Data ISS" ... (id = ?file_type_config=). */
export function fileTypeChoices(meta: ModuleMeta | undefined): { value: string; label: string }[] {
  return (meta?.file_type_configs ?? []).map((c) => ({ value: String(c.id), label: c.label }));
}

/** The short form of the server's status label ("Staged (raw rows ...)" -> "Staged"). */
export const shortStatus = (label: string) => label.split(' (')[0] ?? label;

/** Business date, or for CBS still waiting: the batch date it was uploaded with. */
export function fileDateText(f: Pick<UploadedFileRow, 'business_date' | 'batch_business_date'>): string {
  if (f.business_date) return formatDate(f.business_date);
  if (f.batch_business_date) return `not chosen yet (uploaded for ${formatDate(f.batch_business_date)})`;
  return '-';
}

export function FilesPage() {
  const me = useMe();
  const module = useModuleCode();
  const { data: meta } = useMeta(module);
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => fileFiltersFromParams(params), [params]);

  const setFilter = (key: FilterKey | 'page', value: string | null) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== 'page') next.delete('page');
      return next;
    });
  };

  const active = useUserActive();
  const processing = (page?: FileListPage) => (page?.processing_count ?? 0) > 0;
  const query = useQuery({
    queryKey: ['files', module, filters],
    queryFn: () => listFiles(module, filters),
    placeholderData: keepPreviousData,
    refetchInterval: (q) => pollInterval(processing(q.state.data), active),
  });
  const polling = pollingState(processing(query.data), active);

  const rows = query.data?.results ?? [];
  const count = query.data?.count ?? 0;
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const page = filters.page ?? 1;
  const first = count === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const last = Math.min(count, page * PAGE_SIZE);
  const opt = (list: { value: string; label: string }[]) => [{ value: '', label: 'Any' }, ...list];
  const statusData = opt(fileStatusChoices(meta));
  // A comma list from a home-page link ("VALIDATION_FAILED,FAILED") is shown as one choice.
  if (filters.status && !statusData.some((s) => s.value === filters.status)) {
    statusData.push({
      value: filters.status,
      label: filters.status
        .split(',')
        .map((s) => statusData.find((x) => x.value === s)?.label ?? s)
        .join(' / '),
    });
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={1} size="h3">
          Files
        </Title>
        {can(me.permissions, 'files.upload') && (
          <Button component={Link} to={`/${module}/files/upload`}>
            Upload files
          </Button>
        )}
      </Group>

      <Group gap="xs" align="flex-end" wrap="wrap" component="section" aria-label="Filters">
        <DateInput
          label="Business date"
          valueFormat="DD-MMM-YYYY"
          value={filters.business_date ?? null}
          onChange={(v) => setFilter('business_date', v)}
          clearable
          w={150}
        />
        <Select
          label="File type"
          // One entry per FileTypeConfig, so ACQ and ISS are separate choices.
          data={opt(fileTypeChoices(meta))}
          value={filters.file_type_config ?? ''}
          onChange={(v) => setFilter('file_type_config', v)}
          w={260}
          allowDeselect={false}
          searchable
        />
        <Select
          label="Cycle"
          data={opt((meta?.settlement_cycles ?? []).map((c) => ({ value: c.code, label: c.code })))}
          value={filters.cycle ?? ''}
          onChange={(v) => setFilter('cycle', v)}
          w={100}
          allowDeselect={false}
        />
        <Select
          label="Status"
          data={statusData}
          value={filters.status ?? ''}
          onChange={(v) => setFilter('status', v)}
          w={210}
          allowDeselect={false}
        />
        <Select
          label="Current"
          data={[
            { value: '', label: 'Any' },
            { value: 'true', label: 'Current only' },
            { value: 'false', label: 'Not current' },
          ]}
          value={filters.current ?? ''}
          onChange={(v) => setFilter('current', v)}
          w={130}
          allowDeselect={false}
        />
        <Select
          label="Sort"
          data={ORDERINGS}
          value={filters.ordering ?? ''}
          onChange={(v) => setFilter('ordering', v)}
          w={230}
          allowDeselect={false}
        />
        <Button variant="subtle" onClick={() => setParams(new URLSearchParams())}>
          Reset filters
        </Button>
      </Group>

      <AutoRefresh
        polling={polling}
        what={`${formatCount(query.data?.processing_count ?? 0)} file(s) are being processed`}
        onRefresh={() => void query.refetch()}
        busy={query.isFetching}
      />

      {query.error && (
        <Alert color="red" role="alert" title="The files could not be loaded">
          {query.error.message}
        </Alert>
      )}

      <Text size="sm" aria-live="polite">
        {query.isPending
          ? 'Loading files...'
          : `Showing ${formatCount(first)}-${formatCount(last)} of ${formatCount(count)} files`}
      </Text>

      <Table.ScrollContainer minWidth={1100}>
        <Table aria-busy={query.isFetching} aria-label="Uploaded files">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">File</Table.Th>
              <Table.Th scope="col">Type</Table.Th>
              <Table.Th scope="col">Cycle</Table.Th>
              <Table.Th scope="col">Business date</Table.Th>
              <Table.Th scope="col">Status</Table.Th>
              <Table.Th scope="col">Rows valid / rejected</Table.Th>
              <Table.Th scope="col">Exceptions (E / W / I)</Table.Th>
              <Table.Th scope="col">Uploaded</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((f) => (
              <Table.Tr key={f.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/${module}/files/${f.id}`} className="wrap-anywhere">
                    {f.filename}
                  </Anchor>
                  {!f.is_current && (
                    <Text size="xs" c="dimmed">
                      not current
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{f.file_type.label}</Table.Td>
                <Table.Td>{f.cycle ?? '-'}</Table.Td>
                <Table.Td>{fileDateText(f)}</Table.Td>
                <Table.Td>
                  <FileStateBadge state={f.state} title={f.processing_status_label} />
                  <Text size="xs" c="dimmed">
                    {shortStatus(f.processing_status_label)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {formatCount(f.valid_record_count)} / {formatCount(f.rejected_record_count)}
                </Table.Td>
                <Table.Td>
                  {f.exception_counts
                    ? `${f.exception_counts.ERROR} / ${f.exception_counts.WARNING} / ${f.exception_counts.INFO}`
                    : '-'}
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{formatDateTime(f.uploaded_at)}</Text>
                  <Text size="xs" c="dimmed">
                    {f.uploaded_by ?? '-'}
                  </Text>
                </Table.Td>
              </Table.Tr>
            ))}
            {!query.isPending && rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={8}>No files match these filters.</Table.Td>
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
