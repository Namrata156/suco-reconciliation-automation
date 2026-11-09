/**
 * Case detail and history (design 4.5) with every case action (4.6). The
 * case type, rule set, evaluation time and the rule's facts sit in a
 * collapsed "Details" (UI refinements 2026-10-27, 6.4). A "What happened"
 * box leads (U2 6.3; contract A4): CBS vs NPCI, the reply and where the case
 * stands for the SSKUPI file. The main buttons name the reply (6.5): "Submit
 * reply DRC 102 - Online Reversal", "Approve DRC 102 - Online Reversal".
 * Buttons appear only for the actions the server lists in
 * available_transitions. A 409 (someone else acted first) reloads the case
 * and says so.
 */
import {
  Alert,
  Anchor,
  Button,
  Card,
  Group,
  List,
  Modal,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Link, useParams } from 'react-router';

import { ApiError, caseTransition, getCase, listUsers } from '../api/client';
import type {
  CaseDetail,
  ModuleMeta,
  ProposedActionInfo,
  TransitionName,
  TransitionPayloads,
  WhatHappened,
} from '../api/types';
import {
  ClosedBySystemBadge,
  DecisionBadge,
  FlagBadges,
  SelfApprovedBadge,
  StatusBadge,
} from '../components/Badges';
import {
  ApproveForm,
  ClearReviewForm,
  CompleteForm,
  ReassignForm,
  RejectForm,
  RemarkForm,
  RemarksOnlyForm,
  SubmitActionForm,
} from '../features/cases/actionForms';
import { DetailsSection } from '../components/DetailsSection';
import { OverlayRowsTable } from '../components/OverlayRowsTable';
import { SettlementBlock } from '../features/settlement/SettlementBlock';
import { ContributingEntries } from '../features/settlement/SettlementTransactions';
import { labelOf, useMeta, useModuleCode } from '../hooks/module';
import { formatDate, formatDateTime, formatRupees } from '../lib/format';

const TRANSITION_LABELS: Record<TransitionName, string> = {
  take: 'Take',
  release: 'Release',
  reassign: 'Reassign',
  submit: 'Submit action',
  approve: 'Approve',
  reject: 'Reject',
  complete: 'Mark done',
  remark: 'Add remark',
  clear_review: 'Clear review flag',
};
/** Button order: the main step first, the destructive / secondary ones later. */
const ORDER: TransitionName[] = [
  'take',
  'submit',
  'approve',
  'complete',
  'reject',
  'release',
  'reassign',
  'clear_review',
  'remark',
];

/**
 * A button's text: the reply it acts on when the server names it (contract
 * A4 reply.label) - the rule's or the Maker's reply for "submit", the Maker's
 * submitted reply for "approve" - else the generic label.
 */
export function transitionLabel(
  name: TransitionName,
  c: Pick<CaseDetail, 'what_happened'> & { reply?: CaseDetail['reply'] },
): string {
  const reply = c.reply ?? c.what_happened?.reply ?? null;
  if (name === 'submit' && reply?.label && reply.source !== 'APPROVED') return `Submit reply ${reply.label}`;
  if (name === 'approve' && reply?.label && reply.source === 'SUBMITTED') return `Approve ${reply.label}`;
  return TRANSITION_LABELS[name];
}

/** "Rs 140.50 DEBIT" / "not there". */
function legLine(l: { present: boolean; amount: string | null; direction: string | null }): string {
  if (!l.present) return 'Not there';
  return `${formatRupees(l.amount)}${l.direction ? ` ${l.direction}` : ''}`;
}

const NPCI_STATUS: Record<string, string> = { APPROVED: 'approved', DECLINED: 'declined' };

/** 6.3: CBS vs NPCI, the reply and the SSKUPI status, in plain words. */
function WhatHappenedBox({ module, w }: { module: string; w: WhatHappened }) {
  const s = w.sskupi;
  return (
    <Card withBorder padding="sm" component="section" aria-labelledby="what-happened-title">
      <Title order={2} size="h4" id="what-happened-title" mb="xs">
        What happened
      </Title>
      <SimpleGrid cols={{ base: 1, sm: 2, lg: 4 }} spacing="sm">
        <Field label="In CBS">{legLine(w.cbs)}</Field>
        <Field label="In NPCI">
          {legLine(w.npci)}
          {w.npci.present && (w.npci.response_code || w.npci.status) && (
            <Text size="xs" c="dimmed">
              {[
                w.npci.response_code ? `code ${w.npci.response_code}` : '',
                w.npci.status ? (NPCI_STATUS[w.npci.status] ?? w.npci.status) : '',
              ]
                .filter(Boolean)
                .join(', ')}
            </Text>
          )}
        </Field>
        <Field label="NPCI reply">
          {w.reply?.label ?? 'No reply chosen yet'}
          {w.reply?.label && (
            <Text size="xs" c="dimmed">
              {w.reply.source_label}
            </Text>
          )}
        </Field>
        <Field label="SSKUPI file">
          <Text size="sm" fw={600}>
            {s.file ? (
              <>
                In{' '}
                <Anchor component={Link} to={`/${module}/dates/${s.file.file_date}#step-sskupi`}>
                  {s.file.file_name}
                </Anchor>
                , line {s.file.line}
              </>
            ) : (
              s.status_label
            )}
          </Text>
          {s.file && (
            <Text size="xs" c="dimmed">
              Generated by {s.file.generated_by || '-'} on {formatDateTime(s.file.generated_at)}
            </Text>
          )}
          {!s.file && s.file_date && (
            <Text size="xs" c="dimmed">
              Goes into a file of {formatDate(s.file_date)}
            </Text>
          )}
        </Field>
      </SimpleGrid>
      {s.detail && (
        <Text size="sm" mt="xs">
          {s.reason_label ? `${s.reason_label}: ` : ''}
          {s.detail}
        </Text>
      )}
      {s.changed_since_included && (
        <Alert color="orange" mt="xs" title="Changed since it was included">
          The case changed after it went into {s.file?.file_name ?? 'a file'}. The file does not change; any
          correction is made with NPCI outside the app.
        </Alert>
      )}
    </Card>
  );
}

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

function actionLabel(meta: ModuleMeta | undefined, value: string) {
  return meta?.action_types.find((a) => a.value === value)?.label ?? value;
}

function ActionCard({ a, meta }: { a: ProposedActionInfo; meta: ModuleMeta | undefined }) {
  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" wrap="wrap">
        <Text fw={600}>
          {actionLabel(meta, a.action_type)}
          {a.response_code ? ` - ${a.response_code}` : ''}
        </Text>
        <Group gap={4}>
          <DecisionBadge value={a.decision} />
          {a.self_approved && <SelfApprovedBadge />}
        </Group>
      </Group>
      <Text size="sm">
        Submitted by {a.submitted_by} on {formatDateTime(a.submitted_at)}
        {a.rule_code ? ` under rule ${a.rule_code}` : ''}
      </Text>
      {a.remarks && (
        <Text size="sm" className="wrap-anywhere">
          Remarks: {a.remarks}
        </Text>
      )}
      {(a.evidence_cbs_reversal_date || a.evidence_cbs_reversal_amount || a.evidence_cbs_reversal_rrn) && (
        <Text size="sm">
          CBS reversal evidence: {formatDate(a.evidence_cbs_reversal_date)},{' '}
          {formatRupees(a.evidence_cbs_reversal_amount)}, RRN {a.evidence_cbs_reversal_rrn || '-'}
        </Text>
      )}
      {a.evidence_notes && (
        <Text size="sm" className="wrap-anywhere">
          Evidence notes: {a.evidence_notes}
        </Text>
      )}
      {a.decided_by && (
        <Text size="sm">
          {a.decision === 'REJECTED' ? 'Rejected' : 'Decided'} by {a.decided_by} on{' '}
          {formatDateTime(a.decided_at)}
        </Text>
      )}
      {/* A pending action the system withdrew when it closed the case (owner 2026-11-09). */}
      {!a.decided_by && a.decision === 'WITHDRAWN' && a.decided_at && (
        <Text size="sm">Withdrawn by the system on {formatDateTime(a.decided_at)}</Text>
      )}
      {/* Design 7.3: rejection reasons are shown prominently to the maker. */}
      {a.decision === 'REJECTED' && a.decision_remarks && (
        <Alert color="red" title="Rejection reason" mt="xs">
          {a.decision_remarks}
        </Alert>
      )}
      {a.decision !== 'REJECTED' && a.decision_remarks && (
        <Text size="sm">Decision remarks: {a.decision_remarks}</Text>
      )}
      {a.external_reference && (
        <Text size="sm">
          Done: reference {a.external_reference} on {formatDateTime(a.completed_at)}
        </Text>
      )}
    </Card>
  );
}

function renderValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '-';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function CaseDetailPage() {
  const module = useModuleCode();
  const id = Number(useParams().caseId);
  const qc = useQueryClient();
  const { data: meta } = useMeta(module);
  const [open, setOpen] = useState<TransitionName | null>(null);

  const query = useQuery({
    queryKey: ['case', module, id],
    queryFn: () => getCase(module, id),
    enabled: Number.isInteger(id) && id > 0,
  });
  const c = query.data;

  const makers = useQuery({
    queryKey: ['users', 'Maker'],
    queryFn: () => listUsers('Maker'),
    enabled: open === 'reassign',
    staleTime: 5 * 60_000,
  });

  const mutation = useMutation({
    mutationFn: (args: { name: TransitionName; payload: TransitionPayloads[TransitionName] }) =>
      caseTransition(module, id, args.name, args.payload),
    onSuccess: (detail, { name }) => {
      qc.setQueryData(['case', module, id], detail);
      void qc.invalidateQueries({ queryKey: ['cases', module] });
      void qc.invalidateQueries({ queryKey: ['summary', module] });
      notifications.show({ color: 'green', title: transitionLabel(name, detail), message: 'Saved.' });
      setOpen(null);
    },
  });

  /** Forms call this; errors other than 409 go back to the form. */
  const run = async <N extends TransitionName>(name: N, payload: TransitionPayloads[N]) => {
    try {
      await mutation.mutateAsync({ name, payload });
    } catch (e) {
      if (e instanceof ApiError && e.isConflict) {
        setOpen(null);
        notifications.show({
          color: 'orange',
          title: 'The case changed',
          message: `${e.detail} The case has been reloaded.`,
          autoClose: 10_000,
        });
        await qc.invalidateQueries({ queryKey: ['case', module, id] });
        void qc.invalidateQueries({ queryKey: ['cases', module] });
        return;
      }
      throw e;
    }
  };

  if (!Number.isInteger(id) || id <= 0) return <Alert color="red">Invalid case address.</Alert>;
  if (query.isPending) return <Skeleton h={300} />;
  if (query.error || !c) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Alert color="red" role="alert" title={notFound ? 'Case not found' : 'The case could not be loaded'}>
        {notFound ? `There is no case ${id} in this module.` : query.error?.message}
      </Alert>
    );
  }

  const isSettlement = c.case_type === 'SETTLEMENT';
  const pending = c.actions.find((a) => a.decision === 'PENDING');
  const transitions = ORDER.filter((t) => c.available_transitions.includes(t));
  const close = () => setOpen(null);

  return (
    <Stack>
      <Anchor component={Link} to={`/${module}/cases`} size="sm">
        Back to the case queue
      </Anchor>
      <Group justify="space-between" align="flex-start" wrap="wrap">
        <div>
          <Title order={1} size="h3" className="wrap-anywhere">
            {isSettlement ? `Settlement (NTSL) - ${formatDate(c.business_date)}` : `Case ${c.rrn}`}
          </Title>
          <Group gap={6} mt={4}>
            <StatusBadge value={c.status} choices={meta?.case_statuses} />
            {c.closed_by_system && <ClosedBySystemBadge fileName={c.npci_file?.file_name} />}
            <FlagBadges flags={c.flags} />
          </Group>
        </div>
        <Group gap="xs" role="group" aria-label="Case actions">
          {transitions.map((t) => (
            <Button
              key={t}
              variant={t === 'reject' || t === 'release' ? 'default' : 'filled'}
              onClick={() => setOpen(t)}
            >
              {transitionLabel(t, c)}
            </Button>
          ))}
          {transitions.length === 0 && (
            <Text size="sm" c="dimmed">
              No action is open to you on this case now.
            </Text>
          )}
        </Group>
      </Group>

      {c.closed_by_system && (
        <Alert color="cyan" variant="light" role="note" aria-label="Closed by system">
          Closed by the system when its no-money reply went into{' '}
          {c.npci_file ? (
            c.npci_file.file_date ? (
              <Anchor component={Link} to={`/${module}/dates/${c.npci_file.file_date}#step-sskupi`}>
                {c.npci_file.file_name}
              </Anchor>
            ) : (
              c.npci_file.file_name
            )
          ) : (
            'an SSKUPI file'
          )}
          . No money moves at the bank, so there is no Maker or Checker step.
        </Alert>
      )}

      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Field label="Business date">{formatDate(c.business_date)}</Field>
        <Field label="Rule">{c.rule_code || '-'}</Field>
        <Field label="Outcome">{labelOf(meta?.status_categories, c.status_category)}</Field>
        <Field label="Held by">{c.assigned_to ?? 'nobody'}</Field>
        <Field label="Due">
          {c.due_date
            ? `${formatDate(c.due_date)}${c.due_cutoff_time ? ` ${c.due_cutoff_time.slice(0, 5)}` : ''}`
            : '-'}
        </Field>
        <Field label="Age">
          {c.age_days ?? '-'} days{c.ageing_bucket ? ` (${c.ageing_bucket})` : ''}
        </Field>
        <Field label="Pre-filled action">
          {c.default_action_type ? actionLabel(meta, c.default_action_type) : 'none (maker chooses)'}
          {c.default_response_code ? ` - ${c.default_response_code}` : ''}
        </Field>
      </SimpleGrid>

      {c.what_happened && <WhatHappenedBox module={module} w={c.what_happened} />}

      {c.warnings.length > 0 && (
        <Alert color="orange" title="Warnings (most important first)">
          <List size="sm" type="ordered">
            {c.warnings.map((w) => (
              <List.Item key={w}>{w}</List.Item>
            ))}
          </List>
        </Alert>
      )}

      {c.rule_result && (
        <Stack gap="xs">
          <Title order={2} size="h4">
            Rule result
          </Title>
          <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
            <Field label="Rule">{c.rule_result.rule_code}</Field>
            <Field label="Deadline (TAT)">{c.rule_result.tat_label || '-'}</Field>
          </SimpleGrid>
          <Text size="sm">
            <strong>Action:</strong> {c.rule_result.action || '-'}
          </Text>
          <Text size="sm">
            <strong>Reason:</strong> {c.rule_result.reason || '-'}
          </Text>
        </Stack>
      )}

      {/* Technical detail, collapsed (UI refinements 2026-10-27, 6.4). */}
      <DetailsSection title="Details" id="case-details-title">
        <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
          <Field label="Case type">{labelOf(meta?.case_types, c.case_type)}</Field>
          {c.rule_result && <Field label="Rule set">{c.rule_result.rule_set}</Field>}
          {c.rule_result && <Field label="Evaluated">{formatDateTime(c.rule_result.evaluated_at)}</Field>}
        </SimpleGrid>
        {c.rule_result && Object.keys(c.rule_result.facts).length > 0 && (
          <Table withTableBorder aria-label="Facts the rule used" maw={700}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Fact</Table.Th>
                <Table.Th scope="col">Value</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {Object.entries(c.rule_result.facts).map(([k, v]) => (
                <Table.Tr key={k}>
                  <Table.Td>{k}</Table.Td>
                  <Table.Td className="wrap-anywhere">{renderValue(v)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </DetailsSection>

      {c.settlement && <SettlementBlock s={c.settlement} />}

      {isSettlement && (
        <Stack gap="xs" component="section" aria-labelledby="case-settlement-tx-title">
          <Title order={2} size="h4" id="case-settlement-tx-title">
            Unexplained entries
          </Title>
          <Text size="sm" c="dimmed">
            What no rule explains in the result this case is about; every contributing row is under "All contributing rows".
          </Text>
          <ContributingEntries module={module} source={{ caseId: id }} meta={meta} idPrefix="case" />
        </Stack>
      )}

      {!isSettlement && (
        <Stack gap="xs">
          <Title order={2} size="h4">
            Adjustments for this RRN
          </Title>
          <OverlayRowsTable rows={c.overlay_rows} />
        </Stack>
      )}

      <Stack gap="xs">
        <Title order={2} size="h4">
          Proposed actions
        </Title>
        {c.actions.length === 0 ? (
          <Text size="sm">No action proposed yet.</Text>
        ) : (
          [...c.actions]
            .sort((a, b) => b.submitted_at.localeCompare(a.submitted_at))
            .map((a) => <ActionCard key={a.id} a={a} meta={meta} />)
        )}
      </Stack>

      <Stack gap="xs">
        <Title order={2} size="h4">
          History
        </Title>
        <Table.ScrollContainer minWidth={900}>
          <Table withTableBorder aria-label="Case history">
            <Table.Thead>
              <Table.Tr>
                {['When', 'Event', 'By', 'Status', 'Remarks / changes', 'Source'].map((h) => (
                  <Table.Th key={h} scope="col">
                    {h}
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {c.events.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>{formatDateTime(e.created_at)}</Table.Td>
                  <Table.Td>
                    {labelOf(meta?.event_types, e.event_type)}
                    {e.event_type === 'APPROVED' && e.action_snapshot?.['self_approved'] === true && (
                      <>
                        {' '}
                        <SelfApprovedBadge />
                      </>
                    )}
                  </Table.Td>
                  <Table.Td>{e.actor_username || 'System'}</Table.Td>
                  <Table.Td>
                    {e.old_status && e.new_status && e.old_status !== e.new_status
                      ? `${labelOf(meta?.case_statuses, e.old_status)} -> ${labelOf(meta?.case_statuses, e.new_status)}`
                      : labelOf(meta?.case_statuses, e.new_status)}
                  </Table.Td>
                  <Table.Td className="wrap-anywhere">
                    {e.remarks}
                    {Object.keys(e.changes ?? {}).length > 0 && (
                      <Text size="xs" c="dimmed">
                        {Object.entries(e.changes)
                          .map(
                            ([k, v]) =>
                              `${k}: ${Array.isArray(v) ? `${renderValue(v[0])} -> ${renderValue(v[1])}` : renderValue(v)}`,
                          )
                          .join('; ')}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>{e.source || '-'}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Stack>

      <Modal
        opened={open !== null}
        onClose={close}
        title={open ? transitionLabel(open, c) : ''}
        size={open === 'submit' ? 'lg' : 'md'}
      >
        {open === 'take' && (
          <RemarksOnlyForm
            submitLabel="Take"
            intro="The case will be assigned to you."
            onCancel={close}
            onSubmit={(p) => run('take', p)}
          />
        )}
        {open === 'release' && (
          <RemarksOnlyForm
            submitLabel="Release"
            intro="The case goes back to the shared queue."
            onCancel={close}
            onSubmit={(p) => run('release', p)}
          />
        )}
        {open === 'submit' && meta && (
          <SubmitActionForm
            caseDetail={c}
            meta={meta}
            ruleReplyLabel={c.reply?.source === 'RULE' ? c.reply.label : null}
            onCancel={close}
            onSubmit={(p) => run('submit', p)}
          />
        )}
        {open === 'approve' && (
          <ApproveForm
            selfApproval={c.self_approval}
            selfApprovalMessage={meta?.self_approval_message}
            actionSummary={
              pending
                ? `${actionLabel(meta, pending.action_type)}${pending.response_code ? ` - ${pending.response_code}` : ''} (submitted by ${pending.submitted_by})`
                : undefined
            }
            onCancel={close}
            onSubmit={(p) => run('approve', p)}
          />
        )}
        {open === 'reject' && <RejectForm onCancel={close} onSubmit={(p) => run('reject', p)} />}
        {open === 'complete' && <CompleteForm onCancel={close} onSubmit={(p) => run('complete', p)} />}
        {open === 'remark' && <RemarkForm onCancel={close} onSubmit={(p) => run('remark', p)} />}
        {open === 'clear_review' && (
          <ClearReviewForm
            reason={c.needs_review_reason}
            onCancel={close}
            onSubmit={(p) => run('clear_review', p)}
          />
        )}
        {open === 'reassign' &&
          (makers.isPending ? (
            <Skeleton h={120} />
          ) : makers.error ? (
            <Alert color="red">The Maker list could not be loaded: {makers.error.message}</Alert>
          ) : (
            <ReassignForm
              makers={makers.data ?? []}
              currentHolder={c.assigned_to}
              onCancel={close}
              onSubmit={(p) => run('reassign', p)}
            />
          ))}
      </Modal>
    </Stack>
  );
}
