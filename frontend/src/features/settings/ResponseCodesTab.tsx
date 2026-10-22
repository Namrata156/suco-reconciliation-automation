/**
 * Response codes (design 4.16 a; owner 2026-10-20): the module's code table,
 * a Maker proposes an edit or a new code with a reason, a Checker approves
 * or rejects it in the request history. A code the active rule set uses is
 * locked: only its unknown numeric code may be filled in (the server checks).
 */
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Alert,
  Badge,
  Button,
  Group,
  Modal,
  Select,
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
import { Controller, useForm } from 'react-hook-form';
import { z } from 'zod';

import { listCodeRequests, listResponseCodes, proposeCodeChange } from '../../api/client';
import type { CodeChangeProposal, CodeChangeRequest, ModuleMeta, ResponseCodeRow } from '../../api/types';
import { useMe } from '../../auth/auth';
import { SelfApprovedBadge, VersionStatusBadge } from '../../components/Badges';
import { ListPager } from '../../components/ListPager';
import { labelOf, useMeta, useModuleCode } from '../../hooks/module';
import { displayValue, formatDateTime } from '../../lib/format';
import { can } from '../../lib/roles';
import { pageBounds } from '../../lib/urlFilters';
import { Actions, applyApiError, RootError } from '../cases/actionForms';
import { DecisionModal, invalidateSettings, type DecisionTarget } from './DecisionModal';
import { SettingsEvents } from './SettingsEvents';

const PAGE_SIZE = 50;
const optional = (s: string) => (s.trim() ? s.trim() : undefined);

// ---------------------------------------------------------------------------
// forms
// ---------------------------------------------------------------------------
export const editCodeSchema = z.object({
  new_code: z.string().trim().max(10, 'At most 10 characters'),
  new_description: z.string(),
  new_notes: z.string(),
  reason: z.string().trim().min(1, 'A reason (and the source of the change) is required'),
});

/** Only the fields that differ from the code are sent (left out = unchanged). */
export function editPayload(
  code: ResponseCodeRow,
  v: z.infer<typeof editCodeSchema>,
): CodeChangeProposal | null {
  const p: CodeChangeProposal = { kind: 'EDIT', response_code: code.id, reason: v.reason.trim() };
  if (v.new_code.trim() !== (code.code ?? '')) p.new_code = v.new_code.trim();
  if (v.new_description.trim() !== code.description) p.new_description = v.new_description.trim();
  if (v.new_notes.trim() !== code.notes) p.new_notes = v.new_notes.trim();
  return p.new_code === undefined && p.new_description === undefined && p.new_notes === undefined ? null : p;
}

export function EditCodeForm({
  code,
  onSubmit,
  onCancel,
}: {
  code: ResponseCodeRow;
  onSubmit: (p: CodeChangeProposal) => Promise<void>;
  onCancel: () => void;
}) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof editCodeSchema>>({
    resolver: zodResolver(editCodeSchema),
    defaultValues: {
      new_code: code.code ?? '',
      new_description: code.description,
      new_notes: code.notes,
      reason: '',
    },
  });
  const submit = handleSubmit(async (v) => {
    const payload = editPayload(code, v);
    if (!payload) {
      setError('root', { message: 'Change at least one field.' });
      return;
    }
    try {
      await onSubmit(payload);
    } catch (e) {
      applyApiError(e, setError, ['new_code', 'new_description', 'new_notes', 'reason']);
    }
  });
  const codeLocked = code.locked && code.code_known;
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <Text size="sm">Editing {code.label}. A Checker approves the change before it applies.</Text>
        {code.locked && (
          <Alert color="yellow" title="Used by the active rule set">
            {code.used_by.length ? `Rules ${code.used_by.join(', ')} use this code. ` : ''}Only an unknown
            numeric code may be filled in; description and notes cannot change.
          </Alert>
        )}
        <RootError message={errors.root?.message} />
        <TextInput
          label="Numeric code"
          disabled={codeLocked}
          {...register('new_code')}
          error={errors.new_code?.message}
        />
        <TextInput label="Description" disabled={code.locked} {...register('new_description')} />
        <Textarea label="Notes" disabled={code.locked} autosize minRows={2} {...register('new_notes')} />
        <Textarea
          label="Reason"
          required
          autosize
          minRows={2}
          {...register('reason')}
          error={errors.reason?.message}
        />
        <Actions submitLabel="Submit code change" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

export const createCodeSchema = z.object({
  flag: z.string().min(1, 'Choose the flag'),
  new_code: z.string().trim().max(10, 'At most 10 characters'),
  new_description: z.string().trim().min(1, 'A description is required'),
  new_source: z.string().trim().min(1, 'The source (e.g. the NPCI circular) is required'),
  new_notes: z.string(),
  reason: z.string().trim().min(1, 'A reason is required'),
});

export function CreateCodeForm({
  flags,
  onSubmit,
  onCancel,
}: {
  flags: { value: string; label: string }[];
  onSubmit: (p: CodeChangeProposal) => Promise<void>;
  onCancel: () => void;
}) {
  const {
    register,
    control,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof createCodeSchema>>({
    resolver: zodResolver(createCodeSchema),
    defaultValues: { flag: '', new_code: '', new_description: '', new_source: '', new_notes: '', reason: '' },
  });
  const submit = handleSubmit(async (v) => {
    try {
      await onSubmit({
        kind: 'CREATE',
        flag: v.flag,
        new_code: optional(v.new_code),
        new_description: v.new_description.trim(),
        new_source: v.new_source.trim(),
        new_notes: optional(v.new_notes),
        reason: v.reason.trim(),
      });
    } catch (e) {
      applyApiError(e, setError, [
        'flag',
        'new_code',
        'new_description',
        'new_source',
        'new_notes',
        'reason',
      ]);
    }
  });
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <RootError message={errors.root?.message} />
        <Controller
          control={control}
          name="flag"
          render={({ field }) => (
            <Select
              label="Flag"
              required
              data={flags}
              value={field.value || null}
              onChange={(v) => field.onChange(v ?? '')}
              error={errors.flag?.message}
            />
          )}
        />
        <TextInput
          label="Numeric code (leave empty if not known yet)"
          {...register('new_code')}
          error={errors.new_code?.message}
        />
        <TextInput
          label="Description"
          required
          {...register('new_description')}
          error={errors.new_description?.message}
        />
        <TextInput label="Source" required {...register('new_source')} error={errors.new_source?.message} />
        <Textarea label="Notes" autosize minRows={2} {...register('new_notes')} />
        <Textarea
          label="Reason"
          required
          autosize
          minRows={2}
          {...register('reason')}
          error={errors.reason?.message}
        />
        <Actions submitLabel="Submit new code" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

// ---------------------------------------------------------------------------
// request history
// ---------------------------------------------------------------------------
/**
 * What a request is about: the server's label of the code (contract D2), or
 * "New RET code" for a CREATE not yet approved (label null).
 */
export function requestTarget(r: CodeChangeRequest): string {
  return r.response_code_label ?? `New ${r.flag} code`;
}

export function changeText(changes: CodeChangeRequest['changes']): string {
  return Object.entries(changes)
    .map(([f, [a, b]]) => `${f}: ${displayValue(a)} -> ${displayValue(b)}`)
    .join('; ');
}

function RequestHistory({
  module,
  meta,
  canDecide,
  onDecide,
}: {
  module: string;
  meta: ModuleMeta | undefined;
  canDecide: boolean;
  onDecide: (t: DecisionTarget) => void;
}) {
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ['settings', module, 'code-requests', status, page],
    queryFn: () => listCodeRequests(module, { status, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const { pages } = pageBounds(query.data?.count ?? 0, page, PAGE_SIZE);
  // The requests list says who may decide (contract D3); the codes list's answer until it arrives.
  const mayDecide = query.data?.can_decide ?? canDecide;
  const decide = (r: CodeChangeRequest, decision: 'approve' | 'reject') =>
    onDecide({
      kind: 'response_code',
      id: r.id,
      title: `code request #${r.id}`,
      selfApproval: r.self_approval,
      decision,
    });

  return (
    <Stack gap="xs" component="section" aria-labelledby="code-requests-title">
      <Title order={2} size="h4" id="code-requests-title">
        Change requests
      </Title>
      <Select
        label="Request status"
        data={[{ value: '', label: 'Any' }, ...(meta?.change_request_statuses ?? [])]}
        value={status}
        onChange={(v) => {
          setStatus(v ?? '');
          setPage(1);
        }}
        allowDeselect={false}
        w={220}
      />
      {query.error && (
        <Alert color="red" role="alert">
          {query.error.message}
        </Alert>
      )}
      <Table.ScrollContainer minWidth={1100}>
        <Table withTableBorder aria-label="Response code change requests" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {['Request', 'Code', 'Changes', 'Reason', 'Status', 'Proposed', 'Decision', 'Actions'].map(
                (h) => (
                  <Table.Th key={h} scope="col">
                    {h}
                  </Table.Th>
                ),
              )}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(query.data?.results ?? []).map((r) => (
              <Table.Tr key={r.id}>
                <Table.Td>
                  #{r.id}
                  <Text size="xs" c="dimmed">
                    {labelOf(
                      [
                        { value: 'EDIT', label: 'Edit' },
                        { value: 'CREATE', label: 'New code' },
                      ],
                      r.kind,
                    )}
                  </Text>
                </Table.Td>
                <Table.Td>{requestTarget(r)}</Table.Td>
                <Table.Td className="wrap-anywhere">
                  {changeText(r.changes) || '-'}
                  {r.new_source && (
                    <Text size="xs" c="dimmed">
                      Source: {r.new_source}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td className="wrap-anywhere">{r.reason}</Table.Td>
                <Table.Td>
                  <VersionStatusBadge value={r.status} /> {r.self_approved && <SelfApprovedBadge />}
                </Table.Td>
                <Table.Td>
                  {r.requested_by ?? '-'}
                  <Text size="xs" c="dimmed">
                    {formatDateTime(r.requested_at)}
                  </Text>
                </Table.Td>
                <Table.Td className="wrap-anywhere">
                  {r.decided_by ? `${r.decided_by}, ${formatDateTime(r.decided_at)}` : '-'}
                  {r.decision_remarks && (
                    <Text size="xs" c={r.status === 'REJECTED' ? 'red' : 'dimmed'}>
                      {r.decision_remarks}
                    </Text>
                  )}
                  <SettingsEvents events={r.events} label={`History of code request #${r.id}`} />
                </Table.Td>
                <Table.Td>
                  {r.status === 'PENDING' && mayDecide ? (
                    <Group gap={4}>
                      <Button size="xs" onClick={() => decide(r, 'approve')}>
                        Approve code request #{r.id}
                      </Button>
                      <Button size="xs" variant="default" onClick={() => decide(r, 'reject')}>
                        Reject code request #{r.id}
                      </Button>
                    </Group>
                  ) : r.status === 'PENDING' ? (
                    <Text size="xs" c="dimmed">
                      Awaiting a Checker{r.self_approval ? ' (your proposal)' : ''}
                    </Text>
                  ) : (
                    '-'
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            {!query.isPending && (query.data?.results.length ?? 0) === 0 && (
              <Table.Tr>
                <Table.Td colSpan={8}>No change requests.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ListPager total={pages} value={page} onChange={setPage} label="Change requests" />
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// tab
// ---------------------------------------------------------------------------
export function ResponseCodesTab() {
  const module = useModuleCode();
  const me = useMe();
  const qc = useQueryClient();
  const { data: meta } = useMeta(module);
  const [flag, setFlag] = useState('');
  const [known, setKnown] = useState('');
  const [page, setPage] = useState(1);
  const [editing, setEditing] = useState<ResponseCodeRow | null>(null);
  const [creating, setCreating] = useState(false);
  const [decision, setDecision] = useState<DecisionTarget | null>(null);

  const query = useQuery({
    queryKey: ['settings', module, 'response-codes', flag, known, page],
    queryFn: () => listResponseCodes(module, { flag, known, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const data = query.data;
  // The server says who may act (can_propose / can_decide); /me until the list arrives.
  const canPropose = data?.can_propose ?? can(me.permissions, 'settings.propose_codes');
  const canDecide = data?.can_decide ?? can(me.permissions, 'settings.approve');
  const { pages } = pageBounds(data?.count ?? 0, page, PAGE_SIZE);

  const propose = async (p: CodeChangeProposal) => {
    const req = await proposeCodeChange(module, p);
    notifications.show({
      color: 'green',
      title: 'Code change proposed',
      message: `Request #${req.id} awaits a Checker.`,
    });
    invalidateSettings(qc, module);
    setEditing(null);
    setCreating(false);
  };

  return (
    <Stack>
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <Group gap="xs" align="flex-end" component="section" aria-label="Code filters">
          <Select
            label="Flag"
            data={[{ value: '', label: 'Any' }, ...(meta?.code_flags ?? [])]}
            value={flag}
            onChange={(v) => {
              setFlag(v ?? '');
              setPage(1);
            }}
            allowDeselect={false}
            w={260}
          />
          <Select
            label="Numeric code"
            data={[
              { value: '', label: 'Any' },
              { value: 'true', label: 'Known' },
              { value: 'false', label: 'Not known yet' },
            ]}
            value={known}
            onChange={(v) => {
              setKnown(v ?? '');
              setPage(1);
            }}
            allowDeselect={false}
            w={160}
          />
        </Group>
        {canPropose && <Button onClick={() => setCreating(true)}>Propose a new code</Button>}
      </Group>
      <Text size="sm" c="dimmed">
        Active rule set: {data?.rule_set ?? '-'}.{' '}
        {canPropose ? 'Your proposals go to a Checker.' : 'Makers propose changes; Checkers approve them.'}
      </Text>
      {query.error && (
        <Alert color="red" role="alert" title="The response codes could not be loaded">
          {query.error.message}
        </Alert>
      )}
      <Table.ScrollContainer minWidth={1000}>
        <Table withTableBorder aria-label="Response codes" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {['Flag', 'Code', 'Description', 'Source', 'Used by rules', 'Pending', ''].map((h, i) => (
                <Table.Th key={i} scope="col">
                  {h}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(data?.results ?? []).map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td>{c.flag}</Table.Td>
                <Table.Td>
                  {c.code ?? (
                    <Text span c="dimmed">
                      unknown
                    </Text>
                  )}
                </Table.Td>
                <Table.Td className="wrap-anywhere">
                  {c.description}
                  {c.notes && (
                    <Text size="xs" c="dimmed">
                      {c.notes}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td className="wrap-anywhere">{c.source || '-'}</Table.Td>
                <Table.Td>
                  {c.used_by.length ? c.used_by.join(', ') : '-'}{' '}
                  {c.locked && (
                    <Badge size="sm" variant="outline" color="yellow">
                      Locked
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>{c.pending_request_id ? `Request #${c.pending_request_id}` : '-'}</Table.Td>
                <Table.Td>
                  {canPropose && !c.pending_request_id && (
                    <Button
                      size="xs"
                      variant="default"
                      onClick={() => setEditing(c)}
                      aria-label={`Propose edit of ${c.label}`}
                    >
                      Propose edit
                    </Button>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            {!query.isPending && (data?.results.length ?? 0) === 0 && (
              <Table.Tr>
                <Table.Td colSpan={7}>No response codes match.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ListPager total={pages} value={page} onChange={setPage} label="Response codes" />

      <RequestHistory module={module} meta={meta} canDecide={canDecide} onDecide={setDecision} />

      <Modal
        opened={editing !== null}
        onClose={() => setEditing(null)}
        title="Propose a code change"
        size="lg"
      >
        {editing && <EditCodeForm code={editing} onSubmit={propose} onCancel={() => setEditing(null)} />}
      </Modal>
      <Modal
        opened={creating}
        onClose={() => setCreating(false)}
        title="Propose a new response code"
        size="lg"
      >
        <CreateCodeForm
          flags={meta?.code_flags ?? []}
          onSubmit={propose}
          onCancel={() => setCreating(false)}
        />
      </Modal>
      <DecisionModal module={module} target={decision} onClose={() => setDecision(null)} />
    </Stack>
  );
}
