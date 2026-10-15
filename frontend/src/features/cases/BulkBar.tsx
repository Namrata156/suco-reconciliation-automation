/**
 * Bulk bar (design 4.7): bulk take / bulk submit (Maker), bulk approve
 * (Checker). All or nothing. Before anything is sent it explains why the
 * selection cannot go in bulk; the server still decides.
 */
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Alert,
  Button,
  Checkbox,
  Group,
  List,
  Modal,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Controller, useForm } from 'react-hook-form';
import { z } from 'zod';

import { ApiError, bulkAction } from '../../api/client';
import type { BulkName, BulkPayloads, CaseListItem, ModuleMeta, Permissions } from '../../api/types';
import { bulkEligibility } from '../../lib/bulk';
import { can } from '../../lib/roles';

const OP_LABEL: Record<BulkName, string> = { take: 'Take', submit: 'Submit', approve: 'Approve' };

export interface BulkBarProps {
  module: string;
  selected: CaseListItem[];
  permissions: Partial<Permissions>;
  username: string;
  meta: Pick<ModuleMeta, 'action_types' | 'response_codes' | 'bulk_priorities'> | undefined;
  onDone: () => void;
  onClear: () => void;
}

function Reasons({ op, reasons }: { op: BulkName; reasons: string[] }) {
  return (
    <Alert
      color="orange"
      variant="light"
      title={`Bulk ${OP_LABEL[op].toLowerCase()} is not possible for this selection`}
      data-testid={`bulk-${op}-reasons`}
    >
      <List size="sm" spacing={2}>
        {reasons.map((r) => (
          <List.Item key={r}>{r}</List.Item>
        ))}
      </List>
    </Alert>
  );
}

// -- bulk submit dialog --------------------------------------------------------
const bulkSubmitSchema = z.object({
  action_type: z.string(),
  response_code: z.string(),
  remarks: z.string(),
});
type BulkSubmitValues = z.infer<typeof bulkSubmitSchema>;

export function BulkSubmitForm({
  selected,
  username,
  meta,
  onSubmit,
  onCancel,
}: {
  selected: CaseListItem[];
  username: string;
  meta: Pick<ModuleMeta, 'action_types' | 'response_codes'>;
  onSubmit: (p: Omit<BulkPayloads['submit'], 'case_ids'>) => Promise<void>;
  onCancel: () => void;
}) {
  const {
    control,
    register,
    handleSubmit,
    watch,
    setError,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<BulkSubmitValues>({
    resolver: zodResolver(bulkSubmitSchema),
    defaultValues: { action_type: '', response_code: '', remarks: '' },
  });
  const actionType = watch('action_type');
  const eligibility = bulkEligibility('submit', selected, {
    username,
    actionType,
    actionTypes: meta.action_types,
  });
  const shared = [...new Set(selected.map((c) => c.default_action_type))];
  const effective = actionType || (shared.length === 1 ? (shared[0] ?? '') : '');
  const spec = meta.action_types.find((a) => a.value === effective);
  const needsCode = !!spec?.code_flag && spec.fixed_code === null && !!actionType;

  const submit = handleSubmit(async (v) => {
    if (needsCode && !v.response_code) {
      setError('response_code', { message: `Choose the ${spec?.code_flag} reason code` });
      return;
    }
    if (spec?.requires_remarks && !v.remarks.trim()) {
      setError('remarks', { message: 'Remarks (the finding) are required for this action' });
      return;
    }
    try {
      await onSubmit({
        action_type: v.action_type || undefined,
        response_code: v.response_code ? Number(v.response_code) : undefined,
        remarks: v.remarks.trim() || undefined,
      });
    } catch (e) {
      setError('root', { message: e instanceof Error ? e.message : 'Bulk submit failed' });
    }
  });

  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <Text>
          Submit one shared action for {selected.length} {selected.length === 1 ? 'case' : 'cases'}. Either
          all are submitted, or none.
        </Text>
        {errors.root?.message && (
          <Alert color="red" role="alert" title="Nothing was applied">
            {errors.root.message}
          </Alert>
        )}
        <Controller
          control={control}
          name="action_type"
          render={({ field }) => (
            <Select
              label="Action"
              description="Leave empty to use the cases' shared pre-filled action"
              data={meta.action_types
                .filter((a) => !a.settlement_only && !a.requires_cbs_reversal_evidence)
                .map((a) => ({ value: a.value, label: a.label }))}
              value={field.value || null}
              onChange={(v) => {
                field.onChange(v ?? '');
                setValue('response_code', '');
              }}
              clearable
              searchable
            />
          )}
        />
        {needsCode && spec && (
          <Controller
            control={control}
            name="response_code"
            render={({ field }) => (
              <Select
                label={`${spec.code_flag} reason code`}
                required
                data={meta.response_codes
                  .filter((c) => c.flag === spec.code_flag)
                  .map((c) => ({ value: String(c.id), label: c.label }))}
                value={field.value || null}
                onChange={(v) => field.onChange(v ?? '')}
                error={errors.response_code?.message}
                searchable
              />
            )}
          />
        )}
        <Textarea
          label={spec?.requires_remarks ? 'Remarks (the finding)' : 'Remarks (optional)'}
          autosize
          minRows={2}
          {...register('remarks')}
          error={errors.remarks?.message}
        />
        {!eligibility.ok && <Reasons op="submit" reasons={eligibility.reasons} />}
        <Group justify="flex-end">
          <Button variant="default" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting} disabled={!eligibility.ok}>
            Submit {selected.length} for approval
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

// -- bulk approve dialog --------------------------------------------------------
/**
 * Self-approval is server-enforced (api/views.py BulkView): if the checker
 * submitted any selected action, the request needs confirm_self_approval.
 * Rows carry self_approval, so the confirmation is asked up front; a 400 with
 * errors.confirm_self_approval (data changed meanwhile) asks for it then.
 */
export function BulkApproveForm({
  selected,
  onSubmit,
  onCancel,
}: {
  selected: CaseListItem[];
  onSubmit: (p: { remarks?: string; confirm_self_approval?: boolean }) => Promise<void>;
  onCancel: () => void;
}) {
  const count = selected.length;
  const [remarks, setRemarks] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [serverSelfIds, setServerSelfIds] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  const selfIds = [
    ...new Set([...selected.filter((c) => c.self_approval).map((c) => c.id), ...serverSelfIds]),
  ];
  const needsConfirm = selfIds.length > 0;
  const selfLabels = selected.filter((c) => selfIds.includes(c.id)).map((c) => c.rrn || `case ${c.id}`);

  return (
    <form
      noValidate
      onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        setConfirmError(null);
        if (needsConfirm && !confirmed) {
          setConfirmError('Confirm the self-approval to continue');
          return;
        }
        setBusy(true);
        try {
          await onSubmit({
            remarks: remarks.trim() || undefined,
            ...(needsConfirm ? { confirm_self_approval: true } : {}),
          });
        } catch (err) {
          if (err instanceof ApiError && err.needsSelfApprovalConfirmation) {
            setServerSelfIds(err.selfApprovalCaseIds);
            setConfirmed(false);
            setConfirmError('Confirm the self-approval to continue');
          } else {
            setError(err instanceof Error ? err.message : 'Bulk approve failed');
          }
        } finally {
          setBusy(false);
        }
      }}
    >
      <Stack>
        <Text>
          Approve the proposed action of {count} {count === 1 ? 'case' : 'cases'}. Either all are approved, or
          none.
        </Text>
        {needsConfirm && (
          <Alert color="yellow" title="Self-approval" role="alert">
            You submitted the action of {selfIds.length} selected {selfIds.length === 1 ? 'case' : 'cases'}
            {selfLabels.length
              ? ` (${selfLabels.slice(0, 5).join(', ')}${selfLabels.length > 5 ? ', ...' : ''})`
              : ''}
            ; approving {selfIds.length === 1 ? 'it' : 'them'} will be recorded as self-approved.
          </Alert>
        )}
        {error && (
          <Alert color="red" role="alert" title="Nothing was applied">
            {error}
          </Alert>
        )}
        <Textarea
          label="Remarks (optional)"
          autosize
          minRows={2}
          value={remarks}
          onChange={(e) => setRemarks(e.currentTarget.value)}
        />
        {needsConfirm && (
          <Checkbox
            label="I confirm the approval of my own submissions"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.currentTarget.checked)}
            error={confirmError}
          />
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="submit" loading={busy}>
            {needsConfirm ? `Approve ${count} (incl. self-approved)` : `Approve ${count}`}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

// -- the bar ------------------------------------------------------------------------
export function BulkBar({ module, selected, permissions, username, meta, onDone, onClear }: BulkBarProps) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<null | 'submit' | 'approve'>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const maker = can(permissions, 'cases.work');
  const checker = can(permissions, 'cases.approve');
  const ctx = { username, actionTypes: meta?.action_types, bulkPriorities: meta?.bulk_priorities };
  const take = bulkEligibility('take', selected, ctx);
  const submit = bulkEligibility('submit', selected, ctx);
  const approve = bulkEligibility('approve', selected, ctx);
  // The dialog lets the maker choose the action, so "no shared pre-filled
  // action" alone does not block opening it.
  const submitWithChosenAction = bulkEligibility('submit', selected, {
    ...ctx,
    actionType: '(chosen in dialog)',
  });
  const ids = selected.map((c) => c.id);

  const mutation = useMutation({
    mutationFn: (args: { name: BulkName; payload: BulkPayloads[BulkName] }) =>
      bulkAction(module, args.name, args.payload),
    onSuccess: (res, { name }) => {
      notifications.show({
        color: 'green',
        title: `Bulk ${OP_LABEL[name].toLowerCase()} done`,
        message: `${res.applied} ${res.applied === 1 ? 'case' : 'cases'} ${
          { take: 'taken', submit: 'submitted for approval', approve: 'approved' }[name]
        }.`,
      });
      setServerError(null);
      setDialog(null);
      void qc.invalidateQueries({ queryKey: ['cases', module] });
      void qc.invalidateQueries({ queryKey: ['summary', module] });
      onDone();
    },
  });

  const run = async <N extends BulkName>(name: N, payload: BulkPayloads[N]) => {
    try {
      await mutation.mutateAsync({ name, payload });
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : 'Bulk action failed';
      if (e instanceof ApiError && e.isConflict) {
        void qc.invalidateQueries({ queryKey: ['cases', module] });
      }
      setServerError(msg);
      throw e instanceof Error ? e : new Error(msg);
    }
  };

  if (selected.length === 0) return null;
  const visibleReasons: { op: BulkName; reasons: string[] }[] = [];
  if (maker && !take.ok) visibleReasons.push({ op: 'take', reasons: take.reasons });
  if (maker && !submit.ok) visibleReasons.push({ op: 'submit', reasons: submit.reasons });
  if (checker && !approve.ok) visibleReasons.push({ op: 'approve', reasons: approve.reasons });

  return (
    <Paper withBorder shadow="md" p="sm" className="bulk-bar" role="region" aria-label="Bulk actions">
      <Stack gap="xs">
        <Group justify="space-between">
          <Text fw={600} aria-live="polite">
            {selected.length} {selected.length === 1 ? 'case' : 'cases'} selected
          </Text>
          <Group gap="xs">
            {maker && (
              <Button
                size="sm"
                disabled={!take.ok}
                loading={mutation.isPending && mutation.variables?.name === 'take'}
                onClick={() => run('take', { case_ids: ids }).catch(() => undefined)}
              >
                Take selected
              </Button>
            )}
            {maker && (
              <Button size="sm" disabled={!submitWithChosenAction.ok} onClick={() => setDialog('submit')}>
                Submit selected...
              </Button>
            )}
            {checker && (
              <Button size="sm" disabled={!approve.ok} onClick={() => setDialog('approve')}>
                Approve selected...
              </Button>
            )}
            {!maker && !checker && (
              <Text size="sm" c="dimmed">
                Bulk actions are for Makers and Checkers.
              </Text>
            )}
            <Button size="sm" variant="default" onClick={onClear}>
              Clear selection
            </Button>
          </Group>
        </Group>
        {serverError && !dialog && (
          <Alert
            color="red"
            role="alert"
            title="Refused by the server - nothing was applied"
            withCloseButton
            onClose={() => setServerError(null)}
          >
            {serverError}
          </Alert>
        )}
        {visibleReasons.map((r) => (
          <Reasons key={r.op} op={r.op} reasons={r.reasons} />
        ))}
      </Stack>

      <Modal opened={dialog === 'submit'} onClose={() => setDialog(null)} title="Bulk submit" size="lg">
        {meta && dialog === 'submit' && (
          <BulkSubmitForm
            selected={selected}
            username={username}
            meta={meta}
            onCancel={() => setDialog(null)}
            onSubmit={(p) => run('submit', { case_ids: ids, ...p })}
          />
        )}
      </Modal>
      <Modal opened={dialog === 'approve'} onClose={() => setDialog(null)} title="Bulk approve">
        {dialog === 'approve' && (
          <BulkApproveForm
            selected={selected}
            onCancel={() => setDialog(null)}
            onSubmit={(p) => run('approve', { case_ids: ids, ...p })}
          />
        )}
      </Modal>
    </Paper>
  );
}
