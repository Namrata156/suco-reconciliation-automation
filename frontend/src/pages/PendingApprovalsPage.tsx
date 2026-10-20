/**
 * Pending approvals (design 4.16; owner 2026-10-20): every settings proposal
 * of the module awaiting a Checker, oldest first, plus the count of cases
 * awaiting approval (they have their own queue). All five roles read; only
 * Checkers act (the server says so with can_decide).
 */
import { Alert, Anchor, Badge, Button, Card, Group, Select, Stack, Table, Text, Title } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';

import { listPendingApprovals } from '../api/client';
import type {
  PendingApproval,
  SettingsKind,
  StaleToleranceDates,
  ToleranceDecisionResult,
} from '../api/types';
import { ListPager } from '../components/ListPager';
import { DecisionModal, type DecisionTarget } from '../features/settings/DecisionModal';
import { StaleDatesList } from '../features/settings/StaleDatesList';
import { useMeta, useModuleCode } from '../hooks/module';
import { formatCount, formatDateTime } from '../lib/format';
import { pageBounds, useUrlFilters } from '../lib/urlFilters';

const PAGE_SIZE = 50;
const FILTER_KEYS = ['kind'] as const;

/** The settings tab where each kind lives. */
export const SETTINGS_TAB_OF: Record<SettingsKind, string> = {
  response_code: 'response-codes',
  ntsl_mapping: 'ntsl-mapping',
  tolerance: 'tolerance',
};

export function PendingApprovalsPage() {
  const module = useModuleCode();
  const { data: meta } = useMeta(module);
  const { filters, setFilter } = useUrlFilters(FILTER_KEYS, PAGE_SIZE);
  const [decision, setDecision] = useState<DecisionTarget | null>(null);
  const [stale, setStale] = useState<StaleToleranceDates | null>(null);

  const query = useQuery({
    queryKey: ['settings', module, 'pending', filters],
    queryFn: () => listPendingApprovals(module, filters),
    placeholderData: keepPreviousData,
  });
  const data = query.data;
  const { pages } = pageBounds(data?.count ?? 0, filters.page, PAGE_SIZE);
  const open = (i: PendingApproval, d: 'approve' | 'reject') =>
    setDecision({ kind: i.kind, id: i.id, title: i.title, selfApproval: i.self_approval, decision: d });
  const cases = data?.cases_awaiting_approval;

  return (
    <Stack gap="sm">
      <Title order={1} size="h3">
        Pending approvals
      </Title>

      {cases && (
        <Card withBorder padding="sm" maw={420}>
          <Text size="sm" c="dimmed">
            Cases awaiting approval
          </Text>
          <Anchor
            component={Link}
            to={`/${module}/cases?${new URLSearchParams(cases.query)}`}
            fz={28}
            fw={700}
            aria-label={`Cases awaiting approval: ${formatCount(cases.count)}. Open the queue`}
          >
            {formatCount(cases.count)}
          </Anchor>
        </Card>
      )}

      <Group gap="xs" align="flex-end">
        <Select
          label="Kind of change"
          data={[{ value: '', label: 'Any' }, ...(meta?.settings_kinds ?? [])]}
          value={filters.kind ?? ''}
          onChange={(v) => setFilter('kind', v)}
          allowDeselect={false}
          w={280}
        />
      </Group>
      {data && !data.can_decide && (
        <Text size="sm" c="dimmed">
          Only a Checker approves or rejects these changes.
        </Text>
      )}

      {stale && (
        <Alert
          color="blue"
          title="Tolerance approved: no date was re-run"
          withCloseButton
          onClose={() => setStale(null)}
        >
          <StaleDatesList module={module} block={stale} label="Dates to run again" />
        </Alert>
      )}

      {query.error && (
        <Alert color="red" role="alert" title="The pending approvals could not be loaded">
          {query.error.message}
        </Alert>
      )}
      <Text size="sm" aria-live="polite">
        {query.isPending
          ? 'Loading...'
          : `${formatCount(data?.count ?? 0)} settings change(s) awaiting approval`}
      </Text>
      <Table.ScrollContainer minWidth={1000}>
        <Table withTableBorder aria-label="Pending settings approvals" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {['Kind', 'Change', 'Reason', 'Proposed', 'Actions'].map((h) => (
                <Table.Th key={h} scope="col">
                  {h}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(data?.results ?? []).map((i) => (
              <Table.Tr key={`${i.kind}-${i.id}`}>
                <Table.Td>{i.kind_label}</Table.Td>
                <Table.Td className="wrap-anywhere">
                  <Anchor
                    component={Link}
                    to={`/${module}/settings/${SETTINGS_TAB_OF[i.kind]}`}
                    aria-label={`Open ${i.title} in settings`}
                  >
                    {i.title}
                  </Anchor>
                </Table.Td>
                <Table.Td className="wrap-anywhere">{i.reason || '-'}</Table.Td>
                <Table.Td>
                  {i.proposed_by ?? '-'}{' '}
                  {i.self_approval && (
                    <Badge size="sm" color="yellow" variant="light">
                      Your proposal
                    </Badge>
                  )}
                  <Text size="xs" c="dimmed">
                    {formatDateTime(i.proposed_at)}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {data?.can_decide ? (
                    <Group gap={4}>
                      <Button size="xs" onClick={() => open(i, 'approve')} aria-label={`Approve ${i.title}`}>
                        Approve
                      </Button>
                      <Button
                        size="xs"
                        variant="default"
                        onClick={() => open(i, 'reject')}
                        aria-label={`Reject ${i.title}`}
                      >
                        Reject
                      </Button>
                    </Group>
                  ) : (
                    '-'
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            {!query.isPending && (data?.results.length ?? 0) === 0 && (
              <Table.Tr>
                <Table.Td colSpan={5}>Nothing is waiting for approval.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ListPager total={pages} value={filters.page} onChange={(p) => setFilter('page', String(p))} />

      <DecisionModal
        module={module}
        target={decision}
        onClose={() => setDecision(null)}
        onDone={(target, result) => {
          if (target.kind === 'tolerance' && target.decision === 'approve') {
            setStale((result as ToleranceDecisionResult).dates_with_older_tolerance ?? null);
          }
        }}
      />
    </Stack>
  );
}
