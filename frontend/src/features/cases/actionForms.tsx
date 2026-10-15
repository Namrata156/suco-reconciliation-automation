/**
 * The case action forms (design 4.6). Field names and rules follow
 * exception_management/forms.py and services.py; the server re-checks
 * everything. Each form calls onSubmit(payload); if that throws an ApiError
 * the field errors / detail are shown in the form.
 */
import { zodResolver } from '@hookform/resolvers/zod';
import { Alert, Button, Checkbox, Group, Select, Stack, Text, TextInput, Textarea } from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useMemo, useState } from 'react';
import { Controller, useForm, type FieldValues, type Path, type UseFormSetError } from 'react-hook-form';
import { z } from 'zod';

import { ApiError } from '../../api/client';
import type {
  ActionTypeMeta,
  ApprovePayload,
  CaseDetail,
  ModuleMeta,
  ReassignPayload,
  RemarksPayload,
  SubmitPayload,
  UserRef,
} from '../../api/types';

// ---------------------------------------------------------------------------
// shared
// ---------------------------------------------------------------------------
/** Shows a failed request in the form: field errors where they belong, the rest on top. */
export function applyApiError<T extends FieldValues>(
  e: unknown,
  setError: UseFormSetError<T>,
  fields: readonly string[],
): void {
  if (!(e instanceof ApiError)) {
    setError('root', { message: 'The server could not be reached. Please try again.' });
    return;
  }
  const loose: string[] = [];
  for (const [field, messages] of Object.entries(e.fieldErrors)) {
    const text = messages.join(' ');
    if (fields.includes(field)) setError(field as Path<T>, { message: text });
    else loose.push(field === '__all__' ? text : `${field}: ${text}`);
  }
  const top = [e.detail !== 'Invalid input' ? e.detail : '', ...loose].filter(Boolean).join(' ');
  if (top || loose.length === 0) setError('root', { message: top || e.detail });
}

const optional = (s: string | undefined) => {
  const t = (s ?? '').trim();
  return t ? t : undefined;
};

interface FormProps<P> {
  onSubmit: (payload: P) => Promise<void>;
  onCancel: () => void;
}

function Actions({
  submitLabel,
  busy,
  onCancel,
}: {
  submitLabel: string;
  busy: boolean;
  onCancel: () => void;
}) {
  return (
    <Group justify="flex-end" mt="sm">
      <Button variant="default" onClick={onCancel}>
        Cancel
      </Button>
      <Button type="submit" loading={busy}>
        {submitLabel}
      </Button>
    </Group>
  );
}

function RootError({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <Alert color="red" role="alert" title="Not saved">
      {message}
    </Alert>
  );
}

// ---------------------------------------------------------------------------
// take / release (optional remarks)
// ---------------------------------------------------------------------------
export const remarksSchema = z.object({ remarks: z.string() });

export function RemarksOnlyForm({
  submitLabel,
  intro,
  onSubmit,
  onCancel,
}: FormProps<RemarksPayload> & { submitLabel: string; intro?: string }) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof remarksSchema>>({
    resolver: zodResolver(remarksSchema),
    defaultValues: { remarks: '' },
  });
  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit({ remarks: optional(v.remarks) });
    } catch (e) {
      applyApiError(e, setError, ['remarks']);
    }
  });
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        {intro && <Text>{intro}</Text>}
        <RootError message={errors.root?.message} />
        <Textarea label="Remarks (optional)" autosize minRows={2} {...register('remarks')} />
        <Actions submitLabel={submitLabel} busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

// ---------------------------------------------------------------------------
// approve (self-approval confirmation, design 4.6 / Phase 3 P4)
// ---------------------------------------------------------------------------
export const SELF_APPROVAL_TEXT =
  'You submitted this action; approving it will be recorded as self-approved.';

export function approveSchema(selfApproval: boolean) {
  return z
    .object({ remarks: z.string(), confirmSelf: z.boolean() })
    .refine((v) => !selfApproval || v.confirmSelf, {
      path: ['confirmSelf'],
      message: 'Confirm the self-approval to continue',
    });
}

/**
 * Self-approval is server-enforced (api/views.py): approving your own
 * submission needs confirm_self_approval: true, else 400 with
 * errors.confirm_self_approval. The case's self_approval flag asks up front;
 * the 400 (data changed meanwhile) switches the form to the confirmation.
 */
export function ApproveForm({
  selfApproval: selfApprovalProp,
  selfApprovalMessage = SELF_APPROVAL_TEXT,
  actionSummary,
  onSubmit,
  onCancel,
}: FormProps<ApprovePayload> & {
  selfApproval: boolean;
  selfApprovalMessage?: string;
  actionSummary?: string;
}) {
  const [serverSaysSelf, setServerSaysSelf] = useState(false);
  const selfApproval = selfApprovalProp || serverSaysSelf;
  const schema = useMemo(() => approveSchema(selfApproval), [selfApproval]);
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { remarks: '', confirmSelf: false },
  });
  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit({
        remarks: optional(v.remarks),
        ...(selfApproval && v.confirmSelf ? { confirm_self_approval: true } : {}),
      });
    } catch (e) {
      if (e instanceof ApiError && e.needsSelfApprovalConfirmation) {
        setServerSaysSelf(true);
        setError('confirmSelf', { message: 'Confirm the self-approval to continue' });
        return;
      }
      applyApiError(e, setError, ['remarks']);
    }
  });
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        {actionSummary && <Text>Approve: {actionSummary}</Text>}
        {selfApproval && (
          <Alert color="yellow" title="Self-approval" role="alert">
            {selfApprovalMessage}
          </Alert>
        )}
        <RootError message={errors.root?.message} />
        <Textarea label="Remarks (optional)" autosize minRows={2} {...register('remarks')} />
        {selfApproval && (
          <Checkbox
            label="I confirm this approval of my own submission"
            {...register('confirmSelf')}
            error={errors.confirmSelf?.message}
          />
        )}
        <Actions
          submitLabel={selfApproval ? 'Approve (self-approved)' : 'Approve'}
          busy={isSubmitting}
          onCancel={onCancel}
        />
      </Stack>
    </form>
  );
}

// ---------------------------------------------------------------------------
// reject (reason required)
// ---------------------------------------------------------------------------
export const rejectSchema = z.object({ reason: z.string().trim().min(1, 'A rejection reason is required') });

export function RejectForm({ onSubmit, onCancel }: FormProps<{ reason: string }>) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof rejectSchema>>({
    resolver: zodResolver(rejectSchema),
    defaultValues: { reason: '' },
  });
  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit({ reason: v.reason.trim() });
    } catch (e) {
      applyApiError(e, setError, ['reason']);
    }
  });
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <Text>The case goes back to the maker with this reason.</Text>
        <RootError message={errors.root?.message} />
        <Textarea
          label="Reason"
          required
          autosize
          minRows={3}
          {...register('reason')}
          error={errors.reason?.message}
        />
        <Actions submitLabel="Reject" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

// ---------------------------------------------------------------------------
// mark done (external reference required)
// ---------------------------------------------------------------------------
export const completeSchema = z.object({
  external_reference: z
    .string()
    .trim()
    .min(1, 'The NPCI portal / CBS reference is required')
    .max(100, 'At most 100 characters'),
  remarks: z.string(),
});

export function CompleteForm({
  onSubmit,
  onCancel,
}: FormProps<{ external_reference: string; remarks?: string }>) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof completeSchema>>({
    resolver: zodResolver(completeSchema),
    defaultValues: { external_reference: '', remarks: '' },
  });
  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit({ external_reference: v.external_reference.trim(), remarks: optional(v.remarks) });
    } catch (e) {
      applyApiError(e, setError, ['external_reference', 'remarks']);
    }
  });
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <Text>Record the reference after carrying out the approved action in the NPCI portal / CBS.</Text>
        <RootError message={errors.root?.message} />
        <TextInput
          label="NPCI portal / CBS reference"
          required
          maxLength={100}
          {...register('external_reference')}
          error={errors.external_reference?.message}
        />
        <Textarea label="Remarks (optional)" autosize minRows={2} {...register('remarks')} />
        <Actions submitLabel="Mark done" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

// ---------------------------------------------------------------------------
// remark / clear review (text required)
// ---------------------------------------------------------------------------
export const remarkSchema = z.object({ text: z.string().trim().min(1, 'Remark text is required') });

export function RemarkForm({ onSubmit, onCancel }: FormProps<{ text: string }>) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof remarkSchema>>({
    resolver: zodResolver(remarkSchema),
    defaultValues: { text: '' },
  });
  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit({ text: v.text.trim() });
    } catch (e) {
      applyApiError(e, setError, ['text']);
    }
  });
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <RootError message={errors.root?.message} />
        <Textarea
          label="Remark"
          required
          autosize
          minRows={3}
          {...register('text')}
          error={errors.text?.message}
        />
        <Actions submitLabel="Add remark" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

export const clearReviewSchema = z.object({
  remarks: z.string().trim().min(1, 'A remark explaining the review is required'),
});

export function ClearReviewForm({
  reason,
  onSubmit,
  onCancel,
}: FormProps<{ remarks: string }> & { reason?: string }) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof clearReviewSchema>>({
    resolver: zodResolver(clearReviewSchema),
    defaultValues: { remarks: '' },
  });
  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit({ remarks: v.remarks.trim() });
    } catch (e) {
      applyApiError(e, setError, ['remarks']);
    }
  });
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        {reason && <Text>Flagged because: {reason}</Text>}
        <RootError message={errors.root?.message} />
        <Textarea
          label="Why the case is fine as it stands"
          required
          autosize
          minRows={3}
          {...register('remarks')}
          error={errors.remarks?.message}
        />
        <Actions submitLabel="Clear review flag" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

// ---------------------------------------------------------------------------
// reassign (Administrator; the Maker list comes from /api/v1/users/?role=Maker)
// ---------------------------------------------------------------------------
export const reassignSchema = z.object({
  to_user: z.string().min(1, 'Choose the Maker to assign the case to'),
  remarks: z.string(),
});

export function ReassignForm({
  makers,
  currentHolder,
  onSubmit,
  onCancel,
}: FormProps<ReassignPayload> & { makers: UserRef[]; currentHolder: string | null }) {
  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof reassignSchema>>({
    resolver: zodResolver(reassignSchema),
    defaultValues: { to_user: '', remarks: '' },
  });
  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit({ to_user: Number(v.to_user), remarks: optional(v.remarks) });
    } catch (e) {
      applyApiError(e, setError, ['to_user', 'remarks']);
    }
  });
  const data = makers
    .filter((u) => u.username !== currentHolder)
    .map((u) => ({
      value: String(u.id),
      label: u.full_name && u.full_name !== u.username ? `${u.full_name} (${u.username})` : u.username,
    }));
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <Text>Currently held by: {currentHolder ?? 'nobody'}</Text>
        <RootError message={errors.root?.message} />
        <Controller
          control={control}
          name="to_user"
          render={({ field }) => (
            <Select
              label="Assign to (Maker)"
              required
              searchable
              data={data}
              value={field.value || null}
              onChange={(v) => field.onChange(v ?? '')}
              onBlur={field.onBlur}
              error={errors.to_user?.message}
              nothingFoundMessage="No other Maker found"
            />
          )}
        />
        <Textarea label="Remarks (optional)" autosize minRows={2} {...register('remarks')} />
        <Actions submitLabel="Reassign" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

// ---------------------------------------------------------------------------
// submit action (design 4.6)
// ---------------------------------------------------------------------------
const AMOUNT = /^\d{1,16}(\.\d{1,2})?$/;

export interface SubmitContext {
  isSettlement: boolean;
  defaultActionType: string;
  /** str() of the rule's default code, used when the field is left empty. */
  defaultResponseCode: string | null;
  actionTypes: readonly ActionTypeMeta[];
}

export function submitSchema(ctx: SubmitContext) {
  return z
    .object({
      action_type: z.string(),
      response_code: z.string(),
      remarks: z.string(),
      evidence_cbs_reversal_date: z.string(),
      evidence_cbs_reversal_amount: z.string(),
      evidence_cbs_reversal_rrn: z.string(),
      evidence_notes: z.string(),
    })
    .superRefine((v, issue) => {
      const add = (path: string, message: string) =>
        issue.addIssue({ code: 'custom', path: [path], message });
      if (!v.action_type) {
        add('action_type', 'Choose an action (this rule has no default)');
        return;
      }
      const spec = ctx.actionTypes.find((a) => a.value === v.action_type);
      if (!spec) {
        add('action_type', 'Unknown action');
        return;
      }
      if (ctx.isSettlement && !spec.settlement_only)
        add('action_type', 'A settlement case takes only the explanation');
      if (!ctx.isSettlement && spec.settlement_only)
        add('action_type', 'This action is for settlement cases only');
      if (spec.requires_remarks && !v.remarks.trim()) {
        add(
          'remarks',
          ctx.isSettlement ? 'The explanation is required' : 'Remarks (the finding) are required',
        );
      }
      const usesDefaultCode = v.action_type === ctx.defaultActionType && !!ctx.defaultResponseCode;
      if (spec.code_flag && spec.fixed_code === null && !v.response_code && !usesDefaultCode) {
        add('response_code', `Choose the ${spec.code_flag} reason code`);
      }
      if (spec.requires_cbs_reversal_evidence) {
        if (!v.evidence_cbs_reversal_date)
          add('evidence_cbs_reversal_date', 'The CBS reversal date is required');
        const amount = v.evidence_cbs_reversal_amount.trim();
        if (!amount) add('evidence_cbs_reversal_amount', 'The CBS reversal amount is required');
        else if (!AMOUNT.test(amount) || Number(amount) <= 0) {
          add(
            'evidence_cbs_reversal_amount',
            'Enter a positive amount with at most 2 decimals, e.g. 1500.00',
          );
        }
        if (!v.evidence_cbs_reversal_rrn.trim())
          add('evidence_cbs_reversal_rrn', 'The CBS reversal RRN is required');
      } else if (
        v.evidence_cbs_reversal_amount.trim() &&
        !AMOUNT.test(v.evidence_cbs_reversal_amount.trim())
      ) {
        add('evidence_cbs_reversal_amount', 'Enter an amount with at most 2 decimals, e.g. 1500.00');
      }
    });
}
export type SubmitValues = z.infer<ReturnType<typeof submitSchema>>;

/** Pre-fill (exception_management/forms.py SubmitForm). */
export function submitDefaults(
  c: Pick<CaseDetail, 'default_action_type' | 'case_type' | 'rrc_already_sent_on'>,
): SubmitValues {
  const isSettlement = c.case_type === 'SETTLEMENT';
  return {
    action_type: isSettlement ? 'SETTLEMENT_EXPLANATION' : c.default_action_type || '',
    response_code: '',
    remarks: '',
    evidence_cbs_reversal_date: '',
    evidence_cbs_reversal_amount: '',
    evidence_cbs_reversal_rrn: '',
    // A1: the RRC-already-sent warning is pre-filled as evidence.
    evidence_notes: c.rrc_already_sent_on
      ? `RRC already sent for this RRN on ${c.rrc_already_sent_on}; check before crediting`
      : '',
  };
}

export function toSubmitPayload(v: SubmitValues, spec: ActionTypeMeta | undefined): SubmitPayload {
  const p: SubmitPayload = { action_type: v.action_type };
  if (spec?.code_flag && v.response_code) p.response_code = Number(v.response_code);
  if (v.remarks.trim()) p.remarks = v.remarks.trim();
  if (spec?.requires_cbs_reversal_evidence) {
    p.evidence_cbs_reversal_date = v.evidence_cbs_reversal_date;
    p.evidence_cbs_reversal_amount = v.evidence_cbs_reversal_amount.trim();
    p.evidence_cbs_reversal_rrn = v.evidence_cbs_reversal_rrn.trim();
  }
  if (v.evidence_notes.trim()) p.evidence_notes = v.evidence_notes.trim();
  return p;
}

const SUBMIT_FIELDS = [
  'action_type',
  'response_code',
  'remarks',
  'evidence_cbs_reversal_date',
  'evidence_cbs_reversal_amount',
  'evidence_cbs_reversal_rrn',
  'evidence_notes',
] as const;

export function SubmitActionForm({
  caseDetail,
  meta,
  onSubmit,
  onCancel,
}: FormProps<SubmitPayload> & {
  caseDetail: Pick<
    CaseDetail,
    'default_action_type' | 'default_response_code' | 'case_type' | 'rrc_already_sent_on' | 'rule_code'
  >;
  meta: Pick<ModuleMeta, 'action_types' | 'response_codes'>;
}) {
  const isSettlement = caseDetail.case_type === 'SETTLEMENT';
  const ctx: SubmitContext = useMemo(
    () => ({
      isSettlement,
      defaultActionType: caseDetail.default_action_type,
      defaultResponseCode: caseDetail.default_response_code,
      actionTypes: meta.action_types,
    }),
    [isSettlement, caseDetail.default_action_type, caseDetail.default_response_code, meta.action_types],
  );
  const schema = useMemo(() => submitSchema(ctx), [ctx]);
  const {
    control,
    register,
    handleSubmit,
    setError,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<SubmitValues>({ resolver: zodResolver(schema), defaultValues: submitDefaults(caseDetail) });

  const actionType = watch('action_type');
  const spec = meta.action_types.find((a) => a.value === actionType);
  const actionChoices = meta.action_types
    .filter((a) => a.settlement_only === isSettlement)
    .map((a) => ({ value: a.value, label: a.label }));
  const codeChoices = spec?.code_flag
    ? meta.response_codes
        .filter((c) => c.flag === spec.code_flag && (spec.fixed_code === null || c.code === spec.fixed_code))
        .map((c) => ({ value: String(c.id), label: c.label }))
    : [];
  const usesDefaultCode = actionType === caseDetail.default_action_type && !!caseDetail.default_response_code;

  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit(toSubmitPayload(v, spec));
    } catch (e) {
      applyApiError(e, setError, SUBMIT_FIELDS);
    }
  });

  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        {!caseDetail.default_action_type && !isSettlement && (
          <Text size="sm">Rule {caseDetail.rule_code} has no pre-filled action: choose one.</Text>
        )}
        <RootError message={errors.root?.message} />
        <Controller
          control={control}
          name="action_type"
          render={({ field }) => (
            <Select
              label="Action"
              required
              data={actionChoices}
              value={field.value || null}
              onChange={(v) => {
                field.onChange(v ?? '');
                setValue('response_code', '');
              }}
              onBlur={field.onBlur}
              error={errors.action_type?.message}
              allowDeselect={false}
              searchable
            />
          )}
        />
        {spec?.code_flag && spec.fixed_code !== null && (
          <Text size="sm" c="dimmed">
            Sent as {spec.code_flag} {spec.fixed_code} (filled in automatically).
          </Text>
        )}
        {spec?.code_flag && spec.fixed_code === null && (
          <Controller
            control={control}
            name="response_code"
            render={({ field }) => (
              <Select
                label={`${spec.code_flag} reason code`}
                required={!usesDefaultCode}
                description={
                  usesDefaultCode
                    ? `Leave empty to use the rule's code: ${caseDetail.default_response_code}`
                    : undefined
                }
                data={codeChoices}
                value={field.value || null}
                onChange={(v) => field.onChange(v ?? '')}
                onBlur={field.onBlur}
                error={errors.response_code?.message}
                searchable
                clearable
              />
            )}
          />
        )}
        <Textarea
          label={
            isSettlement
              ? 'Explanation of the settlement difference'
              : spec?.requires_remarks
                ? 'Remarks (the finding)'
                : 'Remarks (optional)'
          }
          required={!!spec?.requires_remarks}
          autosize
          minRows={3}
          {...register('remarks')}
          error={errors.remarks?.message}
        />
        {spec?.requires_cbs_reversal_evidence && (
          <fieldset style={{ border: '1px solid #ced4da', borderRadius: 4, padding: 12 }}>
            <legend>CBS reversal already posted (evidence, required)</legend>
            <Stack gap="xs">
              <Controller
                control={control}
                name="evidence_cbs_reversal_date"
                render={({ field }) => (
                  <DateInput
                    label="CBS reversal date"
                    required
                    valueFormat="DD-MMM-YYYY"
                    value={field.value || null}
                    onChange={(v) => field.onChange(v ?? '')}
                    onBlur={field.onBlur}
                    error={errors.evidence_cbs_reversal_date?.message}
                    clearable
                  />
                )}
              />
              <TextInput
                label="CBS reversal amount (Rs)"
                required
                inputMode="decimal"
                {...register('evidence_cbs_reversal_amount')}
                error={errors.evidence_cbs_reversal_amount?.message}
              />
              <TextInput
                label="CBS reversal RRN"
                required
                maxLength={40}
                {...register('evidence_cbs_reversal_rrn')}
                error={errors.evidence_cbs_reversal_rrn?.message}
              />
            </Stack>
          </fieldset>
        )}
        <Textarea label="Evidence notes (optional)" autosize minRows={2} {...register('evidence_notes')} />
        <Actions submitLabel="Submit for approval" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}
