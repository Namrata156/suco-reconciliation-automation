/**
 * Response codes (design 4.16 a; owner 2026-10-20): the module's code table,
 * a Maker proposes an edit or a new code with a reason, a Checker approves
 * or rejects it in the request history. A code the active rule set uses is
 * locked: the list's editable_fields says what a proposal may change (NPCI
 * file design Amendment 1, A7: its description, and an unknown numeric code);
 * the flag, a known numeric code, the notes and the rule links stay locked.
 * The server checks.
 * A code's pending request opens in a drawer with its events; the request
 * history can be narrowed to one code.
 */
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Alert,
  Badge,
  Button,
  Drawer,
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

import { getCodeRequest, listCodeRequests, listResponseCodes, proposeCodeChange } from '../../api/client';
import type {
  CodeChangeProposal,
  CodeChangeRequest,
  ModuleMeta,
  ResponseCodeField,
  ResponseCodeRow,
} from '../../api/types';
import { useMe } from '../../auth/auth';
import { SelfApprovedBadge, VersionStatusBadge } from '../../components/Badges';
import { DetailsSection } from '../../components/DetailsSection';
import { ListPager } from '../../components/ListPager';
import { labelOf, useMeta, useModuleCode } from '../../hooks/module';
import { displayValue, formatCount, formatDateTime } from '../../lib/format';
import { can } from '../../lib/roles';
import { pageBounds } from '../../lib/urlFilters';
import { Actions, applyApiError, RootError } from '../cases/actionForms';
import { DecisionModal, invalidateSettings, type DecisionTarget } from './DecisionModal';
import { SettingsEvents } from './SettingsEvents';

const PAGE_SIZE = 50;
const CODES_PAGE_SIZE = 500;

/**
 * Codes grouped by reply type (UI refinements U2, 7.2): the server's groups
 * in SSKUPI file order (DRC, RRC, TCC, RET, then OTHER), each with its codes;
 * a code without reply_type (older server) goes under its flag.
 */
export function replyGroups(
  rows: ResponseCodeRow[],
  types: { value: string; label: string }[] | undefined,
): { value: string; label: string; rows: ResponseCodeRow[] }[] {
  const out: { value: string; label: string; rows: ResponseCodeRow[] }[] = (types ?? []).map((t) => ({
    value: t.value,
    label: t.label,
    rows: [],
  }));
  for (const r of rows) {
    const key = r.reply_type ?? r.flag;
    let g = out.find((x) => x.value === key);
    if (!g) {
      g = { value: key, label: key === 'OTHER' ? 'Other (not in the SSKUPI file)' : key, rows: [] };
      out.push(g);
    }
    g.rows.push(r);
  }
  return out.filter((g) => g.rows.length > 0);
}
const KINDS = [
  { value: 'EDIT', label: 'Edit' },
  { value: 'CREATE', label: 'New code' },
];
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

/**
 * What a proposal may change: the server's editable_fields, or (from a server
 * that does not send it yet) the rule before Amendment 1: everything on an
 * unlocked code, only an unknown numeric code on a locked one.
 */
export function editableFields(code: ResponseCodeRow): readonly ResponseCodeField[] {
  if (Array.isArray(code.editable_fields)) return code.editable_fields;
  if (!code.locked) return ['code', 'description', 'notes'];
  return code.code_known ? [] : ['code'];
}

/** Only the editable fields that differ from the code are sent (left out = unchanged). */
export function editPayload(
  code: ResponseCodeRow,
  v: z.infer<typeof editCodeSchema>,
): CodeChangeProposal | null {
  const fields = editableFields(code);
  const p: CodeChangeProposal = { kind: 'EDIT', response_code: code.id, reason: v.reason.trim() };
  if (fields.includes('code') && v.new_code.trim() !== (code.code ?? '')) p.new_code = v.new_code.trim();
  if (fields.includes('description') && v.new_description.trim() !== code.description)
    p.new_description = v.new_description.trim();
  if (fields.includes('notes') && v.new_notes.trim() !== code.notes) p.new_notes = v.new_notes.trim();
  return p.new_code === undefined && p.new_description === undefined && p.new_notes === undefined ? null : p;
}

/** What a locked code allows, from its editable fields. */
export function lockedText(fields: readonly ResponseCodeField[]): string {
  const may = [
    fields.includes('description') ? 'its description (wording)' : null,
    fields.includes('code') ? 'its unknown numeric code' : null,
  ].filter(Boolean);
  const locked = fields.includes('code') ? 'The flag, notes and rule links' : 'The flag, numeric code, notes and rule links';
  return may.length
    ? `Only ${may.join(' and ')} may change. ${locked} stay locked.`
    : 'Nothing on this code may change.';
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
  const fields = editableFields(code);
  const editable = (f: ResponseCodeField) => fields.includes(f);
  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <Text size="sm">Editing {code.label}. A Checker approves the change before it applies.</Text>
        {code.locked && (
          <Alert color="yellow" title="Used by the active rule set">
            {code.used_by.length ? `Rules ${code.used_by.join(', ')} use this code. ` : ''}
            {lockedText(fields)}
          </Alert>
        )}
        {editable('description') && (
          <Text size="xs" c="dimmed">
            The description is also the reply text written into the SSKUPI file: an approved change applies to the
            next file generated, not to files already generated.
          </Text>
        )}
        <RootError message={errors.root?.message} />
        <TextInput
          label="Numeric code"
          disabled={!editable('code')}
          {...register('new_code')}
          error={errors.new_code?.message}
        />
        <TextInput
          label="Description"
          disabled={!editable('description')}
          {...register('new_description')}
          error={errors.new_description?.message}
        />
        <Textarea
          label="Notes"
          disabled={!editable('notes')}
          autosize
          minRows={2}
          {...register('new_notes')}
          error={errors.new_notes?.message}
        />
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

/** Every decided status; the history leaves PENDING out (it is listed above, U1 follow-up). */
export const DECIDED_STATUSES = 'APPROVED,REJECTED';

function RequestTable({
  rows,
  label,
  busy,
  loaded,
  mayDecide,
  onDecide,
  empty,
}: {
  rows: CodeChangeRequest[];
  label: string;
  busy: boolean;
  loaded: boolean;
  mayDecide: boolean;
  onDecide: (r: CodeChangeRequest, decision: 'approve' | 'reject') => void;
  empty: string;
}) {
  return (
    <Table.ScrollContainer minWidth={1100}>
      <Table withTableBorder aria-label={label} aria-busy={busy}>
        <Table.Thead>
          <Table.Tr>
            {['Request', 'Code', 'Changes', 'Reason', 'Status', 'Proposed', 'Decision', 'Actions'].map((h) => (
              <Table.Th key={h} scope="col">
                {h}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.id}>
              <Table.Td>
                #{r.id}
                <Text size="xs" c="dimmed">
                  {labelOf(KINDS, r.kind)}
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
                    <Button size="xs" onClick={() => onDecide(r, 'approve')}>
                      Approve code request #{r.id}
                    </Button>
                    <Button size="xs" variant="default" onClick={() => onDecide(r, 'reject')}>
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
          {loaded && rows.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={8}>{empty}</Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/**
 * The pending requests stay in view, since Checkers act on them here; the
 * decided ones are the collapsed history (UI refinements 2026-10-27, 7.3 and
 * the PM's U1 follow-up). Both read the same list, filtered by status (the
 * server takes a comma-separated status list).
 */
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
  const [pendingPage, setPendingPage] = useState(1);
  const [status, setStatus] = useState('');
  const [code, setCode] = useState('');
  const [page, setPage] = useState(1);
  const pending = useQuery({
    queryKey: ['settings', module, 'code-requests', 'PENDING', pendingPage],
    queryFn: () => listCodeRequests(module, { status: 'PENDING', page: pendingPage, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const decided = status || DECIDED_STATUSES;
  const query = useQuery({
    queryKey: ['settings', module, 'code-requests', decided, code, page],
    queryFn: () =>
      listCodeRequests(module, {
        status: decided,
        response_code: code ? Number(code) : undefined,
        page,
        page_size: PAGE_SIZE,
      }),
    placeholderData: keepPreviousData,
  });
  const pendingPages = pageBounds(pending.data?.count ?? 0, pendingPage, PAGE_SIZE).pages;
  const { pages } = pageBounds(query.data?.count ?? 0, page, PAGE_SIZE);
  // The requests list says who may decide (contract D3); the codes list's answer until it arrives.
  const mayDecide = pending.data?.can_decide ?? query.data?.can_decide ?? canDecide;
  const decide = (r: CodeChangeRequest, decision: 'approve' | 'reject') =>
    onDecide({
      kind: 'response_code',
      id: r.id,
      title: `code request #${r.id}`,
      selfApproval: r.self_approval,
      decision,
    });
  const pendingCount = pending.data?.count;
  const decidedCount = query.data?.count;

  return (
    <>
      <Stack gap="xs" component="section" aria-labelledby="code-pending-title">
        <Title order={2} size="h4" id="code-pending-title">
          Pending change requests{pendingCount === undefined ? '' : ` (${pendingCount})`}
        </Title>
        {pending.error && (
          <Alert color="red" role="alert">
            {pending.error.message}
          </Alert>
        )}
        {!pending.isPending && pendingCount === 0 ? (
          <Text size="sm">No change request is awaiting a Checker.</Text>
        ) : (
          <RequestTable
            rows={pending.data?.results ?? []}
            label="Pending response code change requests"
            busy={pending.isFetching}
            loaded={!pending.isPending}
            mayDecide={mayDecide}
            onDecide={decide}
            empty="No change request is awaiting a Checker."
          />
        )}
        <ListPager
          total={pendingPages}
          value={pendingPage}
          onChange={setPendingPage}
          label="Pending change requests"
        />
      </Stack>

      <DetailsSection
        title={`Change requests${decidedCount === undefined ? '' : ` (${decidedCount})`}`}
        id="code-requests-title"
      >
        <Group gap="xs" align="flex-end" wrap="wrap" component="section" aria-label="Request filters">
          <Select
            label="Request status"
            data={[
              { value: '', label: 'Any decided' },
              ...(meta?.change_request_statuses ?? []).filter((s) => s.value !== 'PENDING'),
            ]}
            value={status}
            onChange={(v) => {
              setStatus(v ?? '');
              setPage(1);
            }}
            allowDeselect={false}
            w={220}
          />
          {/* The module's codes (meta); a CREATE not yet approved has no code, so "Any" lists it. */}
          <Select
            label="Code"
            data={[
              { value: '', label: 'Any' },
              ...(meta?.response_codes ?? []).map((c) => ({ value: String(c.id), label: c.label })),
            ]}
            value={code}
            onChange={(v) => {
              setCode(v ?? '');
              setPage(1);
            }}
            allowDeselect={false}
            searchable
            w={340}
          />
        </Group>
        {query.error && (
          <Alert color="red" role="alert">
            {query.error.message}
          </Alert>
        )}
        <RequestTable
          rows={query.data?.results ?? []}
          label="Response code change requests"
          busy={query.isFetching}
          loaded={!query.isPending}
          mayDecide={mayDecide}
          onDecide={decide}
          empty="No decided change requests."
        />
        <ListPager total={pages} value={page} onChange={setPage} label="Change requests" />
      </DetailsSection>
    </>
  );
}

/** One request with its events (contract D4), opened from a code's "Request #N". */
function CodeRequestDrawer({ module, id, onClose }: { module: string; id: number | null; onClose: () => void }) {
  const query = useQuery({
    queryKey: ['settings', module, 'code-request', id],
    queryFn: () => getCodeRequest(module, id as number),
    enabled: id !== null,
  });
  const r = query.data;
  return (
    <Drawer
      opened={id !== null}
      onClose={onClose}
      position="right"
      size="lg"
      title={id !== null ? `Code request #${id}` : ''}
    >
      {query.isPending && id !== null && <Text size="sm">Loading...</Text>}
      {query.error && (
        <Alert color="red" role="alert" title="The request could not be loaded">
          {query.error.message}
        </Alert>
      )}
      {r && (
        <Stack gap="sm">
          <Group gap="xs">
            <Text fw={600}>{requestTarget(r)}</Text>
            <Text size="sm" c="dimmed">
              {labelOf(KINDS, r.kind)}
            </Text>
            <VersionStatusBadge value={r.status} /> {r.self_approved && <SelfApprovedBadge />}
          </Group>
          <Text size="sm" className="wrap-anywhere">
            Changes: {changeText(r.changes) || '-'}
          </Text>
          {r.new_source && <Text size="sm">Source: {r.new_source}</Text>}
          <Text size="sm" className="wrap-anywhere">
            Reason: {r.reason || '-'}
          </Text>
          <Text size="sm">
            Proposed by {r.requested_by ?? '-'}, {formatDateTime(r.requested_at)}
          </Text>
          <Text size="sm" className="wrap-anywhere">
            Decision: {r.decided_by ? `${r.decided_by}, ${formatDateTime(r.decided_at)}` : '-'}
            {r.decision_remarks ? ` (${r.decision_remarks})` : ''}
          </Text>
          <Title order={3} size="h5">
            Events
          </Title>
          {r.events?.length ? (
            <SettingsEvents events={r.events} label={`Events of code request #${r.id}`} />
          ) : (
            <Text size="sm" c="dimmed">
              No events recorded.
            </Text>
          )}
        </Stack>
      )}
    </Drawer>
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
  const [viewing, setViewing] = useState<number | null>(null);

  const query = useQuery({
    queryKey: ['settings', module, 'response-codes', flag, known, page],
    // The whole table in SSKUPI file order (a few dozen codes; contract A6).
    queryFn: () =>
      listResponseCodes(module, { flag, known, ordering: 'reply_type', page, page_size: CODES_PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const data = query.data;
  // The server says who may act (can_propose / can_decide); /me until the list arrives.
  const canPropose = data?.can_propose ?? can(me.permissions, 'settings.propose_codes');
  const canDecide = data?.can_decide ?? can(me.permissions, 'settings.approve');
  const { pages } = pageBounds(data?.count ?? 0, page, CODES_PAGE_SIZE);
  const groups = replyGroups(data?.results ?? [], data?.reply_types);

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
      <Table.ScrollContainer minWidth={900}>
        <Table withTableBorder aria-label="Response codes" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {['Code', 'Text in the SSKUPI file', 'Pending', ''].map((h, i) => (
                <Table.Th key={i} scope="col">
                  {h}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          {groups.map((g) => (
            <Table.Tbody key={g.value}>
              <Table.Tr>
                <Table.Th scope="colgroup" colSpan={4} bg="var(--mantine-color-gray-1)">
                  {g.label} ({formatCount(g.rows.length)})
                </Table.Th>
              </Table.Tr>
              {g.rows.map((c) => (
                <Table.Tr key={c.id}>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>
                    {c.flag}{' '}
                    {c.code ?? (
                      <Text span c="dimmed">
                        (code not known yet)
                      </Text>
                    )}{' '}
                    {c.locked && (
                      <Badge size="sm" variant="outline" color="yellow">
                        Locked
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td className="wrap-anywhere">
                    {c.in_sskupi_file === false ? (
                      <>
                        <Text size="sm">{c.description}</Text>
                        <Text size="xs" c="dimmed">
                          Not an SSKUPI file reply
                        </Text>
                      </>
                    ) : (
                      <Text size="sm" fw={600}>
                        {c.sskupi_text ?? c.description}
                      </Text>
                    )}
                    {c.sskupi_problem && (
                      <Text size="xs" c="red">
                        {c.sskupi_problem}
                      </Text>
                    )}
                    {c.in_sskupi_file && c.sskupi_ready === false && !c.sskupi_problem && (
                      <Text size="xs" c="orange">
                        Left out of the file until its numeric code is known.
                      </Text>
                    )}
                    {/* Source, notes and the rules using it, behind the code's details (7.2). */}
                    <details className="details-section">
                      <summary>
                        <Text size="xs" span>
                          Details
                        </Text>
                      </summary>
                      <Stack gap={0} mt={4}>
                        <Text size="xs">Source: {c.source || '-'}</Text>
                        <Text size="xs">Used by rules: {c.used_by.length ? c.used_by.join(', ') : '-'}</Text>
                        {c.notes && <Text size="xs">Notes: {c.notes}</Text>}
                      </Stack>
                    </details>
                  </Table.Td>
                  <Table.Td>
                    {c.pending_request_id ? (
                      <Button
                        size="compact-sm"
                        variant="subtle"
                        onClick={() => setViewing(c.pending_request_id)}
                        aria-label={`Request #${c.pending_request_id} for ${c.label}`}
                      >
                        Request #{c.pending_request_id}
                      </Button>
                    ) : (
                      '-'
                    )}
                  </Table.Td>
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
            </Table.Tbody>
          ))}
          {!query.isPending && (data?.results.length ?? 0) === 0 && (
            <Table.Tbody>
              <Table.Tr>
                <Table.Td colSpan={4}>No response codes match.</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          )}
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
      <CodeRequestDrawer module={module} id={viewing} onClose={() => setViewing(null)} />
      <DecisionModal module={module} target={decision} onClose={() => setDecision(null)} />
    </Stack>
  );
}
