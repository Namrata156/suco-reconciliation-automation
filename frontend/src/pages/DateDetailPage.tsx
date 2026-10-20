/**
 * One business date (design 4.11, Amendment 1 step 3): the summary, the run
 * panel with the "Run" button (Operations User), the expected-files
 * checklist, files outside the checklist, earlier runs and the operations
 * history. Refreshes itself while a run is active or files are being
 * processed (lib/polling.ts).
 */
import { Alert, Anchor, Card, List, SimpleGrid, Skeleton, Stack, Table, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router';

import { ApiError, getDate, startRun } from '../api/client';
import type { BusinessDateDetail } from '../api/types';
import { useMe } from '../auth/auth';
import { AutoRefresh } from '../components/AutoRefresh';
import { RunStatusBadge, SettlementStatusBadge } from '../components/Badges';
import { Checklist } from '../features/dates/Checklist';
import { RunPanel } from '../features/dates/RunPanel';
import { OperationHistory } from '../features/files/OperationHistory';
import { useModuleCode } from '../hooks/module';
import { formatCount, formatDate, formatDateTime, formatRupees } from '../lib/format';
import { pollInterval, pollingState, useUserActive } from '../lib/polling';
import { can } from '../lib/roles';
import { dateIsBusy } from './DatesPage';

function Tile({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Card withBorder padding="sm">
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <div>{children}</div>
    </Card>
  );
}

export function DateDetailPage() {
  const me = useMe();
  const module = useModuleCode();
  const qc = useQueryClient();
  const businessDate = useParams().businessDate ?? '';
  const active = useUserActive();
  const busy = (d?: BusinessDateDetail) => !!d && dateIsBusy(d);

  const query = useQuery({
    queryKey: ['date', module, businessDate],
    queryFn: () => getDate(module, businessDate),
    enabled: /^\d{4}-\d{2}-\d{2}$/.test(businessDate),
    refetchInterval: (q) => pollInterval(busy(q.state.data), active),
  });
  const d = query.data;
  const polling = pollingState(busy(d), active);

  const run = async () => {
    try {
      const r = await startRun(module, businessDate);
      notifications.show({
        color: 'green',
        title: 'Run started',
        message: `Run #${r.id} for ${formatDate(businessDate)} is queued.`,
      });
    } catch (e) {
      if (e instanceof ApiError && e.isConflict) void query.refetch();
      throw e;
    } finally {
      void qc.invalidateQueries({ queryKey: ['date', module, businessDate] });
      void qc.invalidateQueries({ queryKey: ['dates', module] });
      void qc.invalidateQueries({ queryKey: ['summary', module] });
      void qc.invalidateQueries({ queryKey: ['cases', module] });
    }
  };

  if (!/^\d{4}-\d{2}-\d{2}$/.test(businessDate)) return <Alert color="red">Invalid date address.</Alert>;
  if (query.isPending) return <Skeleton h={300} />;
  if (query.error || !d) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Alert color="red" role="alert" title={notFound ? 'Nothing for this date' : 'The date could not be loaded'}>
        {notFound ? `No files or runs for ${formatDate(businessDate)}.` : query.error?.message}
      </Alert>
    );
  }

  const casesLink = `/${module}/cases?business_date=${d.business_date}&status=all`;

  return (
    <Stack>
      <Anchor component={Link} to={`/${module}/dates`} size="sm">
        Back to the business dates
      </Anchor>
      <Title order={1} size="h3">
        Business date {formatDate(d.business_date)}
      </Title>
      <AutoRefresh
        polling={polling}
        what="a run or file processing is in progress"
        onRefresh={() => void query.refetch()}
        busy={query.isFetching}
      />
      {d.archived && (
        <Alert color="gray" title="Archived">
          This date has been archived. Loading more files for it is not supported until the
          reprocessing-after-archival rule is decided.
        </Alert>
      )}

      <SimpleGrid cols={{ base: 2, md: 3, lg: 6 }} spacing="sm">
        <Tile label="Expected files present">
          <Text fw={700}>
            {d.files.expected - d.files.missing} of {d.files.expected}
          </Text>
          <Text size="xs">
            {d.files.loaded} loaded, {d.files.missing} missing
          </Text>
        </Tile>
        <Tile label="Files received">
          <Anchor component={Link} to={`/${module}/files?business_date=${d.business_date}`} fw={700}>
            {formatCount(d.files.files_received)}
          </Anchor>
          <Text size="xs">
            {d.files.files_processing} processing, {d.files.files_awaiting_date_selection} awaiting date,{' '}
            {d.files.files_failed} failed
          </Text>
        </Tile>
        <Tile label="Presence matrix">
          <Text fw={700}>{d.matrix.built ? 'Built' : 'Not built'}</Text>
          {d.matrix.built && (
            <Text size="xs">
              {formatCount(d.matrix.match_groups)} groups, {formatDateTime(d.matrix.built_at)}
            </Text>
          )}
        </Tile>
        <Tile label="Rule evaluation">
          <Text fw={700}>{d.rule_evaluation ? d.rule_evaluation.rule_set : 'Not run'}</Text>
          {d.rule_evaluation && (
            <Text size="xs">
              {formatCount(d.rule_evaluation.group_count)} groups,{' '}
              {formatDateTime(d.rule_evaluation.finished_at ?? d.rule_evaluation.started_at)}
            </Text>
          )}
        </Tile>
        <Tile label="Cases">
          <Anchor component={Link} to={casesLink} fw={700} aria-label={`${d.cases.total} cases: open the list`}>
            {formatCount(d.cases.total)}
          </Anchor>
          <Text size="xs">{formatCount(d.cases.active)} active</Text>
        </Tile>
        <Tile label="Settlement">
          <SettlementStatusBadge value={d.settlement?.status} />
          {d.settlement && <Text size="xs">Unexplained {formatRupees(d.settlement.unexplained_amount)}</Text>}
        </Tile>
      </SimpleGrid>

      <RunPanel overview={d} canRun={can(me.permissions, 'files.upload')} onRun={run} />

      <Stack gap="xs" component="section" aria-labelledby="checklist-title">
        <Title order={2} size="h4" id="checklist-title">
          Expected files
        </Title>
        <Checklist module={module} slots={d.checklist} />
        {d.unexpected_files.length > 0 && (
          <Alert color="orange" title="Files outside the checklist">
            <List size="sm">
              {d.unexpected_files.map((f) => (
                <List.Item key={f.id}>
                  <Anchor component={Link} to={`/${module}/files/${f.id}`}>
                    {f.filename}
                  </Anchor>{' '}
                  ({f.cycle ?? 'no cycle'}, {f.processing_status})
                </List.Item>
              ))}
            </List>
          </Alert>
        )}
      </Stack>

      {d.runs.length > 1 && (
        <Stack gap="xs" component="section" aria-labelledby="runs-title">
          <Title order={2} size="h4" id="runs-title">
            Earlier runs
          </Title>
          <Table aria-label="Runs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Run</Table.Th>
                <Table.Th scope="col">Status</Table.Th>
                <Table.Th scope="col">Started by</Table.Th>
                <Table.Th scope="col">Requested</Table.Th>
                <Table.Th scope="col">Finished</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {d.runs.map((r) => (
                <Table.Tr key={r.id}>
                  <Table.Td>#{r.id}</Table.Td>
                  <Table.Td>
                    <RunStatusBadge value={r.status} />
                  </Table.Td>
                  <Table.Td>{r.requested_by || '-'}</Table.Td>
                  <Table.Td>{formatDateTime(r.requested_at)}</Table.Td>
                  <Table.Td>{formatDateTime(r.finished_at)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Stack>
      )}

      <Stack gap="xs" component="section" aria-labelledby="date-history-title">
        <Title order={2} size="h4" id="date-history-title">
          History
        </Title>
        <OperationHistory entries={d.history} label="Business date history" />
      </Stack>
    </Stack>
  );
}
