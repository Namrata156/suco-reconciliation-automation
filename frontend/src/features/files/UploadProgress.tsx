/**
 * After an upload (UI refinements U4, 2.2 / 2.3): each accepted file's live
 * status (Processing -> Loaded / Failed), a CBS file's business-date choice
 * right here when it reaches "awaiting date", and a big "Go to <date>" button
 * once every accepted file is loaded.
 *
 * Polling follows lib/polling.ts: only while something is still processing
 * AND the user is active, so an unattended upload page never keeps the
 * session open (the "Refresh" button always works). All the files just sent
 * are read with ONE request (files list ?ids=, contract E2).
 */
import { Anchor, Badge, Button, Group, List, Stack, Table, Text } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router';

import { listFiles, selectFileDate } from '../../api/client';
import type { FileState, UploadedFileRow } from '../../api/types';
import { AutoRefresh } from '../../components/AutoRefresh';
import { FileStateBadge } from '../../components/Badges';
import { formatDate } from '../../lib/format';
import { pollInterval, pollingState, useUserActive } from '../../lib/polling';
import type { UploadOutcome } from '../../lib/upload';
import { DateSelection } from './DateSelection';

/** States that will not change by themselves (a waiting CBS file needs a person). */
const SETTLED: readonly FileState[] = ['LOADED', 'FAILED', 'AWAITING_DATE_SELECTION'];

export interface LiveCounts {
  processing: number;
  loaded: number;
  failed: number;
  awaiting: number;
}

export function liveCounts(states: readonly FileState[]): LiveCounts {
  return {
    processing: states.filter((s) => s === 'PROCESSING').length,
    loaded: states.filter((s) => s === 'LOADED').length,
    failed: states.filter((s) => s === 'FAILED').length,
    awaiting: states.filter((s) => s === 'AWAITING_DATE_SELECTION').length,
  };
}

/** "Go to <date>" once every accepted file is loaded. */
export const allLoaded = (states: readonly FileState[]) =>
  states.length > 0 && states.every((s) => s === 'LOADED');

export function UploadProgress({
  module,
  businessDate,
  outcomes,
  canSelectDate,
}: {
  module: string;
  businessDate: string;
  outcomes: UploadOutcome[];
  canSelectDate: boolean;
}) {
  const qc = useQueryClient();
  const active = useUserActive();
  const accepted = outcomes.filter((o) => o.accepted).map((o) => o.accepted!);
  const ids = accepted.map((f) => f.id).join(',');

  // One request for every file just sent (files list ?ids=, contract E2); the
  // rows carry the CBS date choice too.
  const list = useQuery({
    queryKey: ['files', module, 'upload-status', ids],
    queryFn: () => listFiles(module, { ids, page_size: 500 }),
    enabled: accepted.length > 0,
    refetchInterval: (q) => {
      const rows = q.state.data?.results ?? [];
      const moving = accepted.some(
        (f) => !SETTLED.includes(rows.find((r) => r.id === f.id)?.state ?? f.state),
      );
      return pollInterval(moving, active);
    },
  });

  const live = (f: UploadedFileRow): UploadedFileRow => list.data?.results.find((r) => r.id === f.id) ?? f;
  const states = accepted.map((f) => live(f).state);
  const counts = liveCounts(states);
  const wanted = counts.processing > 0;
  const refresh = () => void list.refetch();

  // The file resumes processing: read the rows again (and keep polling).
  const confirm = async (id: number, isoDate: string) => {
    const detail = await selectFileDate(module, id, isoDate);
    qc.setQueryData(['file', module, id], detail);
    void list.refetch();
    void qc.invalidateQueries({ queryKey: ['date', module] });
    void qc.invalidateQueries({ queryKey: ['dates', module] });
  };

  const awaiting = accepted
    .map((f) => live(f))
    .filter((f) => f.state === 'AWAITING_DATE_SELECTION' && !!f.date_selection);

  return (
    <Stack gap="sm">
      {accepted.length > 0 && (
        <>
          <Text size="sm" role="status" aria-live="polite">
            {[
              counts.processing ? `${counts.processing} processing` : '',
              `${counts.loaded} of ${accepted.length} loaded`,
              counts.awaiting ? `${counts.awaiting} waiting for the CBS business date` : '',
              counts.failed ? `${counts.failed} failed` : '',
            ]
              .filter(Boolean)
              .join(', ')}
            .
          </Text>
          <AutoRefresh
            polling={pollingState(wanted, active)}
            what="the accepted files are being processed"
            onRefresh={refresh}
            busy={list.isFetching}
          />
        </>
      )}

      {allLoaded(states) && (
        <Group>
          <Button component={Link} to={`/${module}/dates/${businessDate}`} size="lg">
            Go to {formatDate(businessDate)}
          </Button>
          <Text size="sm">Every accepted file is loaded: the date can be run.</Text>
        </Group>
      )}
      {!allLoaded(states) && counts.processing === 0 && counts.awaiting === 0 && counts.failed > 0 && (
        <Text size="sm">
          {counts.failed} file(s) failed: open them to see why.{' '}
          <Anchor component={Link} to={`/${module}/dates/${businessDate}`}>
            Go to {formatDate(businessDate)}
          </Anchor>
        </Text>
      )}

      {awaiting.map((f) => (
        <Stack key={f.id} gap={4}>
          <Text size="sm" fw={600} className="wrap-anywhere">
            {f.filename}
          </Text>
          <DateSelection
            selection={f.date_selection!}
            canSelect={canSelectDate}
            onSelect={(d) => confirm(f.id, d)}
            titleId={`date-selection-${f.id}`}
            confirmLabel={(d) => `Confirm ${formatDate(d)}`}
          />
        </Stack>
      ))}

      <Table.ScrollContainer minWidth={900}>
        <Table aria-label="Upload results">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">File</Table.Th>
              <Table.Th scope="col">Result</Table.Th>
              <Table.Th scope="col">Status now</Table.Th>
              <Table.Th scope="col">Details</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {outcomes.map((o) => {
              const f = o.accepted ? live(o.accepted) : null;
              return (
                <Table.Tr key={o.index}>
                  <Table.Td className="wrap-anywhere">
                    {f ? (
                      <Anchor component={Link} to={`/${module}/files/${f.id}`}>
                        {o.filename}
                      </Anchor>
                    ) : (
                      o.filename
                    )}
                  </Table.Td>
                  <Table.Td>
                    {f ? (
                      <Badge color="green" variant="light">
                        Accepted
                      </Badge>
                    ) : (
                      <Badge color="red" variant="filled">
                        Refused
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {f ? <FileStateBadge state={f.state} title={f.processing_status_label} /> : '-'}
                  </Table.Td>
                  <Table.Td>
                    {f ? (
                      <Text size="sm">
                        {f.file_type.label}
                        {f.cycle ? ` ${f.cycle}` : ''}, file #{f.id}
                        {f.business_date && f.business_date !== businessDate
                          ? `, business date ${formatDate(f.business_date)}`
                          : ''}
                      </Text>
                    ) : (
                      <List size="sm">
                        {o.errors.map((e) => (
                          <List.Item key={e} className="wrap-anywhere">
                            {e}
                          </List.Item>
                        ))}
                      </List>
                    )}
                  </Table.Td>
                </Table.Tr>
              );
            })}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  );
}
