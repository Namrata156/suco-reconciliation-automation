/**
 * "Run reconciliation" for one business date (design 4.11, P6 a, Amendment 1
 * step 3): the last run with its three steps, why a run cannot start now
 * (run_blockers from the server) and the Run button. Missing files do not
 * block (P6: completeness is shown, not assumed), so the confirmation lists
 * them. Operations User only; the server decides and records who started it.
 */
import { Alert, Button, Card, Group, List, Modal, Stack, Table, Text, Title } from '@mantine/core';
import { useState } from 'react';

import { ApiError } from '../../api/client';
import type { BusinessDateOverview, ReconciliationRunInfo, RunStepName } from '../../api/types';
import { RunStatusBadge } from '../../components/Badges';
import { formatDate, formatDateTime } from '../../lib/format';

export const RUN_STEPS: { key: RunStepName; label: string }[] = [
  { key: 'PRESENCE', label: 'Presence matrix' },
  { key: 'RULES', label: 'Rule evaluation + case sync' },
  { key: 'SETTLEMENT', label: 'Settlement reconciliation' },
];

export const isRunActive = (run: ReconciliationRunInfo | null | undefined) =>
  run?.status === 'QUEUED' || run?.status === 'RUNNING';

export function RunSteps({ run }: { run: ReconciliationRunInfo }) {
  return (
    <Table aria-label={`Steps of run #${run.id}`} withRowBorders={false} verticalSpacing={2}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th scope="col">Step</Table.Th>
          <Table.Th scope="col">Status</Table.Th>
          <Table.Th scope="col">Finished</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {RUN_STEPS.map((s) => {
          const step = run.steps[s.key];
          return (
            <Table.Tr key={s.key}>
              <Table.Td>{s.label}</Table.Td>
              <Table.Td>
                <RunStatusBadge value={step?.status ?? 'PENDING'} />
              </Table.Td>
              <Table.Td>{formatDateTime(step?.finished_at)}</Table.Td>
            </Table.Tr>
          );
        })}
      </Table.Tbody>
    </Table>
  );
}

export function RunPanel({
  overview,
  canRun,
  onRun,
}: {
  overview: BusinessDateOverview;
  canRun: boolean;
  /** Throws an ApiError when the server refuses; the refusal is shown here. */
  onRun: () => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const run = overview.last_run;
  const blocked = overview.run_blockers.length > 0;
  const missing = overview.missing;

  const start = async () => {
    setBusy(true);
    setRefusal(null);
    try {
      await onRun();
      setConfirming(false);
    } catch (e) {
      setConfirming(false);
      setRefusal(
        e instanceof ApiError
          ? [e.detail, ...Object.values(e.fieldErrors).flat()].join(' ')
          : 'The server could not be reached.',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card withBorder padding="md" component="section" aria-labelledby="run-title">
      <Stack gap="sm">
        <Title order={2} size="h4" id="run-title">
          Reconciliation run
        </Title>
        {run ? (
          <Stack gap={4} role="group" aria-label="Last run">
            <Group gap="xs">
              <Text size="sm" fw={600}>
                Last run #{run.id}:
              </Text>
              <RunStatusBadge value={run.status} />
              <Text size="sm">
                started by {run.requested_by || '-'} on {formatDateTime(run.requested_at)}
                {run.finished_at ? `, finished ${formatDateTime(run.finished_at)}` : ''}
              </Text>
            </Group>
            <RunSteps run={run} />
            {run.status === 'FAILED' && run.error && (
              <Alert color="red" title="The run failed">
                {run.error}
              </Alert>
            )}
          </Stack>
        ) : (
          <Text size="sm">No run for this date yet.</Text>
        )}

        {overview.run_blockers.length > 0 && (
          <Alert color="gray" title="A run cannot start now">
            <List size="sm">
              {overview.run_blockers.map((b) => (
                <List.Item key={b.code}>{b.message}</List.Item>
              ))}
            </List>
          </Alert>
        )}
        {refusal && (
          <Alert color="red" role="alert" title="The run was not started">
            {refusal}
          </Alert>
        )}

        {canRun ? (
          <Group>
            <Button onClick={() => setConfirming(true)} disabled={blocked}>
              {run ? 'Run reconciliation again' : 'Run reconciliation'}
            </Button>
          </Group>
        ) : (
          <Text size="sm" c="dimmed">
            An Operations User starts the run.
          </Text>
        )}
      </Stack>

      <Modal
        opened={confirming}
        onClose={() => setConfirming(false)}
        title={`Run reconciliation for ${formatDate(overview.business_date)}?`}
        centered
      >
        <Stack gap="sm">
          <Text size="sm">
            {overview.files.files_loaded} file(s) loaded, {overview.files.loaded} of {overview.files.expected}{' '}
            expected files present.
          </Text>
          {missing.length > 0 ? (
            <Alert color="orange" title={`${missing.length} expected file(s) missing`}>
              <Text size="sm">The run uses only the files loaded now:</Text>
              <Text size="sm" className="wrap-anywhere">
                {missing.map((m) => (m.cycle ? `${m.label} ${m.cycle}` : m.label)).join(', ')}
              </Text>
            </Alert>
          ) : (
            <Text size="sm">Every expected file is present.</Text>
          )}
          {run && <Text size="sm">A new run replaces the results of the earlier one.</Text>}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setConfirming(false)} disabled={busy}>
              Cancel
            </Button>
            <Button onClick={start} loading={busy} data-autofocus>
              Start run
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Card>
  );
}
