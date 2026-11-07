/**
 * Approvals (UI refinements U2, 7.1; contract A5): one inbox with two tabs,
 * "Cases (N)" - the cases whose reply waits for a Checker - and "Settings
 * changes (N)" - every settings proposal of the module awaiting a Checker,
 * oldest first (design 4.16; owner 2026-10-20). It replaces the "Awaiting my
 * approval" and "Pending approvals" menu items; the old addresses
 * (/approvals, /cases?mine=approval) still open it. All five roles read; only
 * Checkers act (the server says so with can_decide). Both counts come from
 * one call (settings/pending/?page_size=1).
 */
import { Alert, Anchor, Badge, Button, Group, Select, Stack, Table, Tabs, Text, Title } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { listCases, listPendingApprovals } from '../api/client';
import type {
  PendingApproval,
  SettingsKind,
  StaleToleranceDates,
  ToleranceDecisionResult,
} from '../api/types';
import { ListPager } from '../components/ListPager';
import { DecisionModal, type DecisionTarget } from '../features/settings/DecisionModal';
import { SettingsEvents } from '../features/settings/SettingsEvents';
import { StaleDatesList } from '../features/settings/StaleDatesList';
import { useMeta, useModuleCode } from '../hooks/module';
import { formatCount, formatDate, formatDateTime, formatRupees } from '../lib/format';
import { pageBounds, useUrlFilters } from '../lib/urlFilters';

const PAGE_SIZE = 50;
const FILTER_KEYS = ['kind'] as const;

/** The settings tab where each kind lives. */
export const SETTINGS_TAB_OF: Record<SettingsKind, string> = {
  response_code: 'response-codes',
  ntsl_mapping: 'ntsl-mapping',
  tolerance: 'tolerance',
};

const CASES_PAGE_SIZE = 50;

/** The Cases tab: every case whose reply waits for a Checker (any Checker may approve any). */
function CasesAwaitingApproval({ module }: { module: string }) {
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ['cases', module, 'approvals-tab', page],
    queryFn: () => listCases(module, { status: 'PENDING_APPROVAL', page, page_size: CASES_PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const rows = query.data?.results ?? [];
  const { pages } = pageBounds(query.data?.count ?? 0, page, CASES_PAGE_SIZE);
  return (
    <Stack gap="sm">
      <Group gap="sm" wrap="wrap">
        <Text size="sm">Open a case to approve or reject its reply.</Text>
        <Anchor component={Link} to={`/${module}/cases?status=PENDING_APPROVAL`} size="sm">
          Approve several at once in the case queue
        </Anchor>
      </Group>
      {query.error && (
        <Alert color="red" role="alert" title="The cases could not be loaded">
          {query.error.message}
        </Alert>
      )}
      <Table.ScrollContainer minWidth={1000}>
        <Table withTableBorder aria-label="Cases awaiting approval" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {['Case', 'Business date', 'NPCI reply', 'Amount', 'Rule', 'Submitted'].map((h) => (
                <Table.Th key={h} scope="col">
                  {h}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td className="wrap-anywhere">
                  <Anchor component={Link} to={`/${module}/cases/${c.id}`}>
                    {c.case_type === 'SETTLEMENT' ? `Settlement (NTSL) #${c.id}` : c.rrn}
                  </Anchor>
                </Table.Td>
                <Table.Td>{formatDate(c.business_date)}</Table.Td>
                <Table.Td>{c.reply?.label ?? '-'}</Table.Td>
                <Table.Td ta="right">{c.amount ? formatRupees(c.amount) : '-'}</Table.Td>
                <Table.Td>{c.rule_name || c.rule_code || '-'}</Table.Td>
                <Table.Td>
                  {c.self_approval ? (
                    <Badge size="sm" color="yellow" variant="light">
                      Your submission
                    </Badge>
                  ) : (
                    '-'
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            {!query.isPending && rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={6}>No case is waiting for approval.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ListPager total={pages} value={page} onChange={setPage} label="Cases awaiting approval" />
    </Stack>
  );
}

export const APPROVAL_TABS = ['cases', 'settings'] as const;

/** The inbox: Cases (N) | Settings changes (N); the tab is kept in the address (?tab=). */
export function ApprovalsPage() {
  const module = useModuleCode();
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') === 'settings' ? 'settings' : 'cases';
  const counts = useQuery({
    queryKey: ['settings', module, 'pending', 'counts'],
    queryFn: () => listPendingApprovals(module, { page_size: 1 }),
  });
  const n = (v: number | undefined) => (v === undefined ? '' : ` (${formatCount(v)})`);
  return (
    <Stack gap="sm">
      <Title order={1} size="h3">
        Approvals
      </Title>
      <Tabs value={tab} onChange={(v) => v && setParams(v === 'cases' ? {} : { tab: v })} keepMounted={false}>
        <Tabs.List aria-label="Approvals">
          <Tabs.Tab value="cases">Cases{n(counts.data?.cases_awaiting_approval.count)}</Tabs.Tab>
          <Tabs.Tab value="settings">Settings changes{n(counts.data?.count)}</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="cases" pt="sm">
          <CasesAwaitingApproval module={module} />
        </Tabs.Panel>
        <Tabs.Panel value="settings" pt="sm">
          <PendingApprovalsPage />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}

/** The "Settings changes" tab (the former Pending approvals screen). */
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

  return (
    <Stack gap="sm">
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
                  {/* A code request's "detail" carries its events (contract D4). */}
                  {i.kind === 'response_code' && 'events' in i.detail && (
                    <SettingsEvents events={i.detail.events} label={`History of ${i.title}`} />
                  )}
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
