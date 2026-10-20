/**
 * Settlement tolerance (design 4.16 c; owner 2026-10-20): Administrator
 * proposes amount and count with a reason, a Checker approves or rejects.
 * Results already computed keep the tolerance they used; the dates settled
 * with an older one are listed, each linking to its overview to run again.
 */
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Alert,
  Button,
  Card,
  Group,
  Modal,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';

import { getTolerance, listToleranceVersions, proposeTolerance } from '../../api/client';
import type { StaleToleranceDates, ToleranceDecisionResult, ToleranceProposal } from '../../api/types';
import { SelfApprovedBadge, VersionStatusBadge } from '../../components/Badges';
import { Field } from '../../components/Field';
import { ListPager } from '../../components/ListPager';
import { useModuleCode } from '../../hooks/module';
import { formatCount, formatDateTime, formatRupees } from '../../lib/format';
import { pageBounds } from '../../lib/urlFilters';
import { Actions, applyApiError, RootError } from '../cases/actionForms';
import { DecisionModal, invalidateSettings, type DecisionTarget } from './DecisionModal';
import { SettingsEvents } from './SettingsEvents';
import { StaleDatesList } from './StaleDatesList';

export const toleranceSchema = z.object({
  amount: z
    .string()
    .trim()
    .regex(/^\d{1,13}(\.\d{1,2})?$/, 'Enter rupees with at most 2 decimals, e.g. 1.00'),
  txn_count: z
    .string()
    .trim()
    .regex(/^\d{1,9}$/, 'Enter a whole number of transactions, e.g. 0'),
  reason: z.string().trim().min(1, 'A reason is required'),
});

export function ToleranceForm({
  current,
  onSubmit,
  onCancel,
}: {
  current: { amount: string; txn_count: number };
  onSubmit: (p: ToleranceProposal) => Promise<void>;
  onCancel: () => void;
}) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof toleranceSchema>>({
    resolver: zodResolver(toleranceSchema),
    defaultValues: { amount: current.amount, txn_count: String(current.txn_count), reason: '' },
  });
  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit({ amount: v.amount.trim(), txn_count: Number(v.txn_count), reason: v.reason.trim() });
    } catch (e) {
      applyApiError(e, setError, ['amount', 'txn_count', 'reason']);
    }
  });
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <Text size="sm">
          Now: {formatRupees(current.amount)} / {formatCount(current.txn_count)} transactions. A Checker
          approves the change; dates already settled keep the tolerance they used until they are run again.
        </Text>
        <RootError message={errors.root?.message} />
        <TextInput
          label="Tolerance amount (Rs)"
          required
          inputMode="decimal"
          {...register('amount')}
          error={errors.amount?.message}
        />
        <TextInput
          label="Tolerance count (transactions)"
          required
          inputMode="numeric"
          {...register('txn_count')}
          error={errors.txn_count?.message}
        />
        <Textarea
          label="Reason"
          required
          autosize
          minRows={2}
          {...register('reason')}
          error={errors.reason?.message}
        />
        <Actions submitLabel="Submit tolerance proposal" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

const VERSIONS_PAGE_SIZE = 20;

export function ToleranceTab() {
  const module = useModuleCode();
  const qc = useQueryClient();
  const [proposing, setProposing] = useState(false);
  const [decision, setDecision] = useState<DecisionTarget | null>(null);
  const [approvedStale, setApprovedStale] = useState<StaleToleranceDates | null>(null);
  const [page, setPage] = useState(1);

  const state = useQuery({
    queryKey: ['settings', module, 'tolerance'],
    queryFn: () => getTolerance(module),
  });
  const versions = useQuery({
    queryKey: ['settings', module, 'tolerance-versions', page],
    queryFn: () => listToleranceVersions(module, { page, page_size: VERSIONS_PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  if (state.isPending) return <Skeleton h={240} />;
  if (state.error || !state.data) {
    return (
      <Alert color="red" role="alert" title="The tolerance could not be loaded">
        {state.error?.message}
      </Alert>
    );
  }
  const t = state.data;
  const current = t.active ?? { ...t.default_if_none };
  const pending = t.pending;
  const { pages } = pageBounds(versions.data?.count ?? 0, page, VERSIONS_PAGE_SIZE);

  const propose = async (p: ToleranceProposal) => {
    const created = await proposeTolerance(module, p);
    notifications.show({
      color: 'green',
      title: 'Tolerance change proposed',
      message: `v${created.version} awaits a Checker.`,
    });
    invalidateSettings(qc, module);
    setProposing(false);
  };

  return (
    <Stack>
      <Card withBorder padding="sm" component="section" aria-labelledby="tolerance-active-title">
        <Title order={2} size="h4" id="tolerance-active-title">
          {t.active ? `Active tolerance v${t.active.version}` : 'Active tolerance (none recorded yet)'}
        </Title>
        <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm" mt="xs">
          <Field label="Amount">
            <Text fw={700}>{formatRupees(current.amount)}</Text>
          </Field>
          <Field label="Transactions">
            <Text fw={700}>{formatCount(current.txn_count)}</Text>
          </Field>
          <Field label="Active since">{t.active ? formatDateTime(t.active.activated_at) : '-'}</Field>
          <Field label="Approved by">
            {t.active?.decided_by ?? '-'} {t.active?.self_approved && <SelfApprovedBadge />}
          </Field>
        </SimpleGrid>
        {!t.active && (
          <Text size="sm" mt="xs" c="dimmed">
            No version yet: the first settlement run applies and records Rs {t.default_if_none.amount} /{' '}
            {t.default_if_none.txn_count}.
          </Text>
        )}
        <Text size="sm" mt="xs" c="dimmed">
          {t.note}
        </Text>
        {t.can_propose && !pending && (
          <Group mt="sm">
            <Button onClick={() => setProposing(true)}>Propose tolerance change</Button>
          </Group>
        )}
      </Card>

      {pending && (
        <Card withBorder padding="sm" component="section" aria-labelledby="tolerance-pending-title">
          <Group justify="space-between" wrap="wrap">
            <Title order={2} size="h4" id="tolerance-pending-title">
              Pending: tolerance v{pending.version}
            </Title>
            <VersionStatusBadge value={pending.status} />
          </Group>
          <Text size="sm" mt="xs">
            {formatRupees(pending.amount)} / {formatCount(pending.txn_count)} transactions, proposed by{' '}
            {pending.proposed_by ?? '-'} on {formatDateTime(pending.proposed_at)}
            {pending.self_approval ? ' (your proposal)' : ''}.
          </Text>
          <Text size="sm">Reason: {pending.reason}</Text>
          <SettingsEvents events={pending.events} label={`History of tolerance v${pending.version}`} />
          {t.can_decide ? (
            <Group mt="sm">
              <Button
                onClick={() =>
                  setDecision({
                    kind: 'tolerance',
                    id: pending.id,
                    title: `tolerance v${pending.version}`,
                    selfApproval: pending.self_approval,
                    decision: 'approve',
                  })
                }
              >
                Approve tolerance v{pending.version}
              </Button>
              <Button
                variant="default"
                onClick={() =>
                  setDecision({
                    kind: 'tolerance',
                    id: pending.id,
                    title: `tolerance v${pending.version}`,
                    selfApproval: pending.self_approval,
                    decision: 'reject',
                  })
                }
              >
                Reject tolerance v{pending.version}
              </Button>
            </Group>
          ) : (
            <Text size="sm" mt="xs" c="dimmed">
              Awaiting a Checker&apos;s decision.
            </Text>
          )}
        </Card>
      )}

      {approvedStale && (
        <Alert
          color="blue"
          title="Approved: no date was re-run"
          withCloseButton
          onClose={() => setApprovedStale(null)}
        >
          {approvedStale.count === 0
            ? 'Every date already used the active tolerance.'
            : `${approvedStale.count} date(s) were settled with an older tolerance; they are listed below.`}
        </Alert>
      )}

      <Stack gap="xs" component="section" aria-labelledby="stale-dates-title">
        <Title order={2} size="h4" id="stale-dates-title">
          Dates settled with an older tolerance
        </Title>
        <StaleDatesList module={module} block={t.dates_with_older_tolerance} />
      </Stack>

      <Stack gap="xs" component="section" aria-labelledby="tolerance-versions-title">
        <Title order={2} size="h4" id="tolerance-versions-title">
          Tolerance history
        </Title>
        {versions.error && (
          <Alert color="red" role="alert">
            {versions.error.message}
          </Alert>
        )}
        <Table.ScrollContainer minWidth={900}>
          <Table withTableBorder aria-label="Tolerance versions">
            <Table.Thead>
              <Table.Tr>
                {[
                  'Version',
                  'Status',
                  'Amount',
                  'Transactions',
                  'Reason',
                  'Proposed',
                  'Decided',
                  'Remarks',
                ].map((h) => (
                  <Table.Th key={h} scope="col">
                    {h}
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(versions.data?.results ?? []).map((v) => (
                <Table.Tr key={v.id}>
                  <Table.Td>v{v.version}</Table.Td>
                  <Table.Td>
                    <VersionStatusBadge value={v.status} /> {v.self_approved && <SelfApprovedBadge />}
                  </Table.Td>
                  <Table.Td ta="right">{formatRupees(v.amount)}</Table.Td>
                  <Table.Td ta="right">{formatCount(v.txn_count)}</Table.Td>
                  <Table.Td className="wrap-anywhere">{v.reason || '-'}</Table.Td>
                  <Table.Td>
                    {v.proposed_by ?? '-'}
                    <Text size="xs" c="dimmed">
                      {formatDateTime(v.proposed_at)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    {v.decided_by ?? '-'}
                    <Text size="xs" c="dimmed">
                      {formatDateTime(v.decided_at)}
                    </Text>
                  </Table.Td>
                  <Table.Td className="wrap-anywhere">{v.decision_remarks || '-'}</Table.Td>
                </Table.Tr>
              ))}
              {!versions.isPending && (versions.data?.results.length ?? 0) === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={8}>No tolerance version yet.</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        <ListPager total={pages} value={page} onChange={setPage} label="Tolerance versions" />
      </Stack>

      <Modal opened={proposing} onClose={() => setProposing(false)} title="Propose a tolerance change">
        <ToleranceForm current={current} onSubmit={propose} onCancel={() => setProposing(false)} />
      </Modal>
      <DecisionModal
        module={module}
        target={decision}
        onClose={() => setDecision(null)}
        onDone={(target, result) => {
          if (target.decision === 'approve') {
            setApprovedStale((result as ToleranceDecisionResult).dates_with_older_tolerance ?? null);
          }
        }}
      />
    </Stack>
  );
}
