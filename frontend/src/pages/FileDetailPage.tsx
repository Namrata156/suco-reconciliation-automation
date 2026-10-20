/**
 * File detail (design 4.9 / 4.10): status, counts, the operations history,
 * the validation exceptions (paged on the server) and, for a CBS file paused
 * at AWAITING_DATE_SELECTION, the business-date selection. Refreshes itself
 * while the file is still being processed (lib/polling.ts).
 */
import {
  Alert,
  Anchor,
  Group,
  Pagination,
  Select,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router';

import { ApiError, getFile, getFileExceptions, selectFileDate } from '../api/client';
import type { UploadedFileDetail } from '../api/types';
import { useMe } from '../auth/auth';
import { AutoRefresh } from '../components/AutoRefresh';
import { FileStateBadge } from '../components/Badges';
import { DateSelection } from '../features/files/DateSelection';
import { OperationHistory } from '../features/files/OperationHistory';
import { useModuleCode } from '../hooks/module';
import { formatBytes, formatCount, formatDate, formatDateTime } from '../lib/format';
import { pollInterval, pollingState, useUserActive } from '../lib/polling';
import { can } from '../lib/roles';
import { fileDateText, shortStatus } from './FilesPage';

const EXC_PAGE_SIZE = 50;

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <div>{children}</div>
    </div>
  );
}

function ValidationExceptions({ module, file }: { module: string; file: UploadedFileDetail }) {
  const [severity, setSeverity] = useState('');
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ['file-exceptions', module, file.id, severity, page],
    queryFn: () =>
      getFileExceptions(module, file.id, { severity: severity || undefined, page, page_size: EXC_PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const rows = query.data?.results ?? [];
  const count = query.data?.count ?? 0;
  const pages = Math.max(1, Math.ceil(count / EXC_PAGE_SIZE));
  return (
    <Stack gap="xs" component="section" aria-labelledby="exc-title">
      <Group justify="space-between" align="flex-end">
        <Title order={2} size="h4" id="exc-title">
          Validation exceptions
        </Title>
        <Select
          label="Severity"
          data={[
            { value: '', label: 'Any' },
            { value: 'ERROR', label: 'Error' },
            { value: 'WARNING', label: 'Warning' },
            { value: 'INFO', label: 'Info' },
          ]}
          value={severity}
          onChange={(v) => {
            setSeverity(v ?? '');
            setPage(1);
          }}
          w={130}
          allowDeselect={false}
        />
      </Group>
      {query.error && (
        <Alert color="red" role="alert">
          {query.error.message}
        </Alert>
      )}
      <Text size="sm" aria-live="polite">
        {query.isPending ? 'Loading...' : `${formatCount(count)} exception(s)`}
      </Text>
      {rows.length > 0 && (
        <Table.ScrollContainer minWidth={800}>
          <Table aria-label="Validation exceptions" aria-busy={query.isFetching}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Severity</Table.Th>
                <Table.Th scope="col">Rule</Table.Th>
                <Table.Th scope="col">Row</Table.Th>
                <Table.Th scope="col">Message</Table.Th>
                <Table.Th scope="col">Detected</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>{e.severity}</Table.Td>
                  <Table.Td>{e.rule_code}</Table.Td>
                  <Table.Td>{e.row_reference || '-'}</Table.Td>
                  <Table.Td className="wrap-anywhere">{e.message}</Table.Td>
                  <Table.Td>{formatDateTime(e.detected_at)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      {pages > 1 && (
        <Pagination
          total={pages}
          value={page}
          onChange={setPage}
          getControlProps={(control) => ({ 'aria-label': `${control} page of exceptions` })}
          getItemProps={(p) => ({ 'aria-label': `Exceptions page ${p}` })}
        />
      )}
    </Stack>
  );
}

export function FileDetailPage() {
  const me = useMe();
  const module = useModuleCode();
  const qc = useQueryClient();
  const id = Number(useParams().fileId);
  const active = useUserActive();
  const processing = (f?: UploadedFileDetail) => f?.state === 'PROCESSING';

  const query = useQuery({
    queryKey: ['file', module, id],
    queryFn: () => getFile(module, id),
    enabled: Number.isInteger(id) && id > 0,
    refetchInterval: (q) => pollInterval(processing(q.state.data), active),
  });
  const f = query.data;
  const polling = pollingState(processing(f), active);

  const select = async (isoDate: string) => {
    try {
      const detail = await selectFileDate(module, id, isoDate);
      qc.setQueryData(['file', module, id], detail);
      notifications.show({
        color: 'green',
        title: 'Business date confirmed',
        message: `${detail.filename}: ${formatDate(isoDate)}. Processing continues.`,
      });
    } catch (e) {
      // 409: no longer waiting (someone else chose) - show the file as it is now.
      if (e instanceof ApiError && e.isConflict) void qc.invalidateQueries({ queryKey: ['file', module, id] });
      throw e;
    } finally {
      void qc.invalidateQueries({ queryKey: ['files', module] });
      void qc.invalidateQueries({ queryKey: ['dates', module] });
      void qc.invalidateQueries({ queryKey: ['date', module] });
      void qc.invalidateQueries({ queryKey: ['summary', module] });
    }
  };

  if (!Number.isInteger(id) || id <= 0) return <Alert color="red">Invalid file address.</Alert>;
  if (query.isPending) return <Skeleton h={300} />;
  if (query.error || !f) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Alert color="red" role="alert" title={notFound ? 'File not found' : 'The file could not be loaded'}>
        {notFound ? `There is no file ${id} in this module.` : query.error?.message}
      </Alert>
    );
  }

  const day = f.business_date ?? f.batch_business_date;

  return (
    <Stack>
      <Anchor component={Link} to={`/${module}/files`} size="sm">
        Back to the files
      </Anchor>
      <div>
        <Title order={1} size="h3" className="wrap-anywhere">
          {f.filename}
        </Title>
        <Group gap={6} mt={4}>
          <FileStateBadge state={f.state} title={f.processing_status_label} />
          <Text size="sm">{shortStatus(f.processing_status_label)}</Text>
        </Group>
      </div>
      <AutoRefresh
        polling={polling}
        what="the file is being processed"
        onRefresh={() => void query.refetch()}
        busy={query.isFetching}
      />

      {f.date_selection && (
        <DateSelection
          key={f.id}
          selection={f.date_selection}
          canSelect={can(me.permissions, 'files.upload')}
          onSelect={select}
        />
      )}

      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Field label="File type">{f.file_type.label}</Field>
        <Field label="Cycle">{f.cycle ?? '-'}</Field>
        <Field label="Business date">
          {f.business_date ? (
            <Anchor component={Link} to={`/${module}/dates/${f.business_date}`}>
              {formatDate(f.business_date)}
            </Anchor>
          ) : (
            fileDateText(f)
          )}
        </Field>
        <Field label="Uploaded">
          {formatDateTime(f.uploaded_at)} by {f.uploaded_by ?? '-'}
        </Field>
        <Field label="Rows valid / rejected">
          {formatCount(f.valid_record_count)} / {formatCount(f.rejected_record_count)}
        </Field>
        <Field label="Size">{formatBytes(f.size_bytes)}</Field>
        <Field label="Format">{f.detected_format || '-'}</Field>
        <Field label="Current">
          {f.is_current ? 'yes' : 'no'}
          {f.is_reprocess ? ' (reprocess)' : ''}
        </Field>
        <Field label="Source">{f.source_system || '-'}</Field>
        <Field label="SHA-256">
          <Text size="xs" className="wrap-anywhere" ff="monospace">
            {f.sha256}
          </Text>
        </Field>
      </SimpleGrid>

      {day && !f.date_selection && (
        <Text size="sm">
          <Anchor component={Link} to={`/${module}/dates/${day}`}>
            Business date {formatDate(day)}: checklist and run
          </Anchor>
        </Text>
      )}

      <ValidationExceptions module={module} file={f} />

      <Stack gap="xs" component="section" aria-labelledby="file-history-title">
        <Title order={2} size="h4" id="file-history-title">
          History
        </Title>
        <OperationHistory entries={f.history} label="File history" />
      </Stack>
    </Stack>
  );
}
