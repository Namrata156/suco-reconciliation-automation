/**
 * NTSL category mapping (design 4.16 b; owner 2026-10-20): the active
 * version, its entries, the version history, and the pending proposal. A
 * Maker proposes entries to add and active entries to remove (ticked in the
 * entries table) with a reason; a Checker approves or rejects. The server
 * refuses a proposal that would leave a loaded NTSL description unmapped.
 */
import { zodResolver } from '@hookform/resolvers/zod';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Group,
  List,
  Modal,
  NumberInput,
  Select,
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
import { useState, type FormEvent } from 'react';
import { Controller, useFieldArray, useForm } from 'react-hook-form';
import { z } from 'zod';

import {
  getMapVersion,
  getNtslMapping,
  listMapEntries,
  listMapVersions,
  proposeMappingChange,
} from '../../api/client';
import type { Choice, MapEntry, MapEntryFields, MappingProposal, MapVersion } from '../../api/types';
import { SelfApprovedBadge, VersionStatusBadge } from '../../components/Badges';
import { ListPager } from '../../components/ListPager';
import { labelOf, useMeta, useModuleCode } from '../../hooks/module';
import { formatCount, formatDateTime } from '../../lib/format';
import { pageBounds } from '../../lib/urlFilters';
import { Actions, applyApiError, RootError } from '../cases/actionForms';
import { DecisionModal, invalidateSettings, type DecisionTarget } from './DecisionModal';
import { SettingsEvents } from './SettingsEvents';

/**
 * The match and txn type choices come from /meta/ (ntsl_match_types,
 * ntsl_txn_types; Slice 4 contract D1), so no list is kept here. REGEX is
 * the one value with its own behaviour (patterns sent verbatim).
 */
const REGEX = 'REGEX';
const DEFAULT_PRIORITY = 100; // settlement.mapping_changes _clean_entry

const ENTRY_PAGE_SIZE = 50;
const VERSION_PAGE_SIZE = 20;

// ---------------------------------------------------------------------------
// proposal form
// ---------------------------------------------------------------------------
const entrySchema = z.object({
  match_type: z.string().min(1, 'Choose the match type'),
  pattern: z.string().refine((s) => s.trim().length > 0, 'The description or regex is required'),
  category: z.string().min(1, 'Choose the category'),
  txn_type: z.string(),
  priority: z.number().int('A whole number'),
  notes: z.string(),
});
export const mappingSchema = z.object({
  add: z.array(entrySchema),
  reason: z.string().trim().min(1, 'A reason (and the source of the change) is required'),
});
type MappingForm = z.infer<typeof mappingSchema>;

/** A new entry starts on the first match type the server lists (EXACT). */
const blankEntry = (matchTypes: Choice[]): MappingForm['add'][number] => ({
  match_type: matchTypes[0]?.value ?? '',
  pattern: '',
  category: '',
  txn_type: '',
  priority: DEFAULT_PRIORITY,
  notes: '',
});

/** The POST body: exact patterns trimmed (as the server stores them), regexes verbatim. */
export function mappingPayload(v: MappingForm, removeIds: number[]): MappingProposal {
  return {
    add: v.add.map((e) => ({
      match_type: e.match_type,
      pattern: e.match_type === REGEX ? e.pattern : e.pattern.trim(),
      category: e.category,
      txn_type: e.txn_type,
      priority: e.priority,
      notes: e.notes.trim(),
    })),
    remove_entry_ids: removeIds,
    reason: v.reason.trim(),
  };
}

export function MappingProposalForm({
  categories,
  matchTypes,
  txnTypes,
  removing,
  onSubmit,
  onCancel,
}: {
  categories: Choice[];
  /** meta.ntsl_match_types */
  matchTypes: Choice[];
  /** meta.ntsl_txn_types ("" = none) */
  txnTypes: Choice[];
  /** Active entries ticked for removal. */
  removing: MapEntry[];
  onSubmit: (p: MappingProposal) => Promise<void>;
  onCancel: () => void;
}) {
  const {
    register,
    control,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<MappingForm>({
    resolver: zodResolver(mappingSchema),
    defaultValues: { add: removing.length ? [] : [blankEntry(matchTypes)], reason: '' },
  });
  const { fields, append, remove } = useFieldArray({ control, name: 'add' });
  const submit = handleSubmit(async (v) => {
    if (v.add.length === 0 && removing.length === 0) {
      setError('root', { message: 'Add an entry or tick entries to remove first.' });
      return;
    }
    try {
      await onSubmit(
        mappingPayload(
          v,
          removing.map((e) => e.id),
        ),
      );
    } catch (e) {
      applyApiError(e, setError, ['reason']);
    }
  });

  return (
    <form onSubmit={submit} noValidate>
      <Stack>
        <Text size="sm">
          The new version is the active one minus the removed entries plus the added ones. A Checker approves
          it before it is used.
        </Text>
        <RootError message={errors.root?.message} />
        <div>
          <Text fw={600} size="sm">
            Entries to remove ({removing.length})
          </Text>
          {removing.length === 0 ? (
            <Text size="sm" c="dimmed">
              None. Tick entries in the active version&apos;s table to remove them.
            </Text>
          ) : (
            <List size="sm">
              {removing.map((e) => (
                <List.Item key={e.id}>
                  #{e.id} {e.match_type} &quot;{e.pattern}&quot; - {e.category_label}
                </List.Item>
              ))}
            </List>
          )}
        </div>
        <Text fw={600} size="sm">
          Entries to add ({fields.length})
        </Text>
        {fields.map((f, i) => {
          const n = i + 1;
          const err = errors.add?.[i];
          return (
            <Card key={f.id} withBorder padding="xs" role="group" aria-label={`New entry ${n}`}>
              <Group gap="xs" align="flex-start" wrap="wrap">
                <Controller
                  control={control}
                  name={`add.${i}.match_type`}
                  render={({ field }) => (
                    <Select
                      label={`Match type ${n}`}
                      data={matchTypes}
                      value={field.value || null}
                      onChange={(v) => field.onChange(v ?? '')}
                      allowDeselect={false}
                      w={220}
                      error={err?.match_type?.message}
                    />
                  )}
                />
                <TextInput
                  label={`Pattern ${n}`}
                  required
                  w={320}
                  {...register(`add.${i}.pattern`)}
                  error={err?.pattern?.message}
                />
                <Controller
                  control={control}
                  name={`add.${i}.category`}
                  render={({ field }) => (
                    <Select
                      label={`Category ${n}`}
                      required
                      data={categories}
                      value={field.value || null}
                      onChange={(v) => field.onChange(v ?? '')}
                      searchable
                      w={300}
                      error={err?.category?.message}
                    />
                  )}
                />
                <Controller
                  control={control}
                  name={`add.${i}.txn_type`}
                  render={({ field }) => (
                    <Select
                      label={`Txn type ${n}`}
                      data={txnTypes}
                      value={field.value}
                      onChange={(v) => field.onChange(v ?? '')}
                      allowDeselect={false}
                      w={100}
                    />
                  )}
                />
                <Controller
                  control={control}
                  name={`add.${i}.priority`}
                  render={({ field }) => (
                    <NumberInput
                      label={`Priority ${n}`}
                      value={field.value}
                      onChange={(v) => field.onChange(typeof v === 'number' ? v : Number(v) || 0)}
                      allowDecimal={false}
                      w={100}
                      error={err?.priority?.message}
                    />
                  )}
                />
                <TextInput label={`Notes ${n}`} w={240} {...register(`add.${i}.notes`)} />
                <Button variant="subtle" color="red" mt={24} onClick={() => remove(i)}>
                  Drop new entry {n}
                </Button>
              </Group>
            </Card>
          );
        })}
        <Group>
          <Button variant="default" onClick={() => append(blankEntry(matchTypes))}>
            Add another entry
          </Button>
        </Group>
        <Textarea
          label="Reason"
          required
          autosize
          minRows={2}
          {...register('reason')}
          error={errors.reason?.message}
        />
        <Actions submitLabel="Submit mapping proposal" busy={isSubmitting} onCancel={onCancel} />
      </Stack>
    </form>
  );
}

// ---------------------------------------------------------------------------
// pieces of the tab
// ---------------------------------------------------------------------------
function EntryList({
  title,
  entries,
  categories,
}: {
  title: string;
  entries: MapEntryFields[];
  categories: Choice[];
}) {
  if (entries.length === 0) return null;
  return (
    <div>
      <Text size="sm" fw={600}>
        {title} ({entries.length})
      </Text>
      <List size="sm">
        {entries.map((e, i) => (
          <List.Item key={i}>
            {e.match_type} &quot;{e.pattern}&quot; - {labelOf(categories, e.category)}
            {e.txn_type ? ` (${e.txn_type})` : ''}, priority {e.priority}
          </List.Item>
        ))}
      </List>
    </div>
  );
}

function PendingVersion({
  module,
  pending,
  categories,
  canDecide,
  onDecide,
}: {
  module: string;
  pending: MapVersion;
  categories: Choice[];
  canDecide: boolean;
  onDecide: (t: DecisionTarget) => void;
}) {
  const detail = useQuery({
    queryKey: ['settings', module, 'map-version', pending.id],
    queryFn: () => getMapVersion(module, pending.id),
  });
  const summary = detail.data?.change_summary;
  const target = (decision: 'approve' | 'reject'): DecisionTarget => ({
    kind: 'ntsl_mapping',
    id: pending.id,
    title: `NTSL mapping v${pending.version}`,
    selfApproval: pending.self_approval,
    decision,
  });
  return (
    <Card withBorder padding="sm" component="section" aria-labelledby="mapping-pending-title">
      <Group justify="space-between" wrap="wrap">
        <Title order={2} size="h4" id="mapping-pending-title">
          Pending: NTSL mapping v{pending.version}
        </Title>
        <VersionStatusBadge value={pending.status} />
      </Group>
      <Text size="sm" mt="xs">
        Based on v{pending.based_on_version ?? '-'}: {pending.change_counts.added} added,{' '}
        {pending.change_counts.removed} removed, {pending.change_counts.recategorised_loaded_descriptions}{' '}
        loaded description(s) recategorised. Proposed by {pending.proposed_by ?? '-'} on{' '}
        {formatDateTime(pending.proposed_at)}
        {pending.self_approval ? ' (your proposal)' : ''}.
      </Text>
      <Text size="sm">Reason: {pending.reason}</Text>
      {detail.isPending && <Skeleton h={60} />}
      {summary && (
        <Stack gap={4} mt="xs">
          <EntryList title="Added" entries={summary.added ?? []} categories={categories} />
          <EntryList title="Removed" entries={summary.removed ?? []} categories={categories} />
          {(summary.recategorised_loaded_descriptions ?? []).length > 0 && (
            <div>
              <Text size="sm" fw={600}>
                Loaded descriptions that change category
              </Text>
              <List size="sm">
                {(summary.recategorised_loaded_descriptions ?? []).map((r) => (
                  <List.Item key={r.description}>
                    {r.description}: {labelOf(categories, r.old)} -&gt; {labelOf(categories, r.new)}
                  </List.Item>
                ))}
              </List>
            </div>
          )}
        </Stack>
      )}
      <SettingsEvents events={detail.data?.events} label={`History of NTSL mapping v${pending.version}`} />
      {canDecide ? (
        <Group mt="sm">
          <Button onClick={() => onDecide(target('approve'))}>Approve NTSL mapping v{pending.version}</Button>
          <Button variant="default" onClick={() => onDecide(target('reject'))}>
            Reject NTSL mapping v{pending.version}
          </Button>
        </Group>
      ) : (
        <Text size="sm" mt="xs" c="dimmed">
          Awaiting a Checker&apos;s decision.
        </Text>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// tab
// ---------------------------------------------------------------------------
export function NtslMappingTab() {
  const module = useModuleCode();
  const { data: meta } = useMeta(module);
  const matchTypes = meta?.ntsl_match_types ?? [];
  const txnTypes = meta?.ntsl_txn_types ?? [];
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number | null>(null);
  const [category, setCategory] = useState('');
  const [matchType, setMatchType] = useState('');
  const [search, setSearch] = useState('');
  const [searchDraft, setSearchDraft] = useState('');
  const [entryPage, setEntryPage] = useState(1);
  const [versionPage, setVersionPage] = useState(1);
  const [removing, setRemoving] = useState<Map<number, MapEntry>>(new Map());
  const [proposing, setProposing] = useState(false);
  const [decision, setDecision] = useState<DecisionTarget | null>(null);

  const state = useQuery({
    queryKey: ['settings', module, 'ntsl-mapping'],
    queryFn: () => getNtslMapping(module),
  });
  const versions = useQuery({
    queryKey: ['settings', module, 'map-versions', versionPage],
    queryFn: () => listMapVersions(module, { page: versionPage, page_size: VERSION_PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const s = state.data;
  const versionId = selected ?? s?.active?.id ?? null;
  const entries = useQuery({
    queryKey: ['settings', module, 'map-entries', versionId, category, matchType, search, entryPage],
    queryFn: () =>
      listMapEntries(module, versionId as number, {
        category,
        match_type: matchType,
        search,
        page: entryPage,
        page_size: ENTRY_PAGE_SIZE,
      }),
    enabled: versionId !== null,
    placeholderData: keepPreviousData,
  });

  if (state.isPending) return <Skeleton h={240} />;
  if (state.error || !s) {
    return (
      <Alert color="red" role="alert" title="The NTSL mapping could not be loaded">
        {state.error?.message}
      </Alert>
    );
  }

  const categories = s.categories;
  const viewingActive = s.active !== null && versionId === s.active.id;
  const canRemove = s.can_propose && !s.pending && viewingActive;
  const shownVersion =
    versions.data?.results.find((v) => v.id === versionId) ?? (viewingActive ? s.active : null);
  const entryBounds = pageBounds(entries.data?.count ?? 0, entryPage, ENTRY_PAGE_SIZE);
  const versionBounds = pageBounds(versions.data?.count ?? 0, versionPage, VERSION_PAGE_SIZE);

  const toggle = (e: MapEntry, on: boolean) =>
    setRemoving((prev) => {
      const next = new Map(prev);
      if (on) next.set(e.id, e);
      else next.delete(e.id);
      return next;
    });
  const applySearch = (e: FormEvent) => {
    e.preventDefault();
    setSearch(searchDraft.trim());
    setEntryPage(1);
  };
  const propose = async (p: MappingProposal) => {
    const v = await proposeMappingChange(module, p);
    notifications.show({
      color: 'green',
      title: 'Mapping change proposed',
      message: `v${v.version} awaits a Checker.`,
    });
    invalidateSettings(qc, module);
    setRemoving(new Map());
    setProposing(false);
  };

  return (
    <Stack>
      <Card withBorder padding="sm" component="section" aria-labelledby="mapping-active-title">
        <Title order={2} size="h4" id="mapping-active-title">
          {s.active ? `Active NTSL mapping v${s.active.version}` : 'No active NTSL mapping'}
        </Title>
        {s.active && (
          <Text size="sm" mt="xs">
            {formatCount(s.active.entry_count)} entries. Active since {formatDateTime(s.active.activated_at)},
            approved by {s.active.decided_by ?? '-'} {s.active.self_approved && <SelfApprovedBadge />}
          </Text>
        )}
        {s.can_propose && !s.pending && s.active && (
          <Group mt="sm">
            <Button onClick={() => setProposing(true)}>
              Propose mapping change{removing.size ? ` (${removing.size} to remove)` : ''}
            </Button>
            {removing.size > 0 && (
              <Button variant="subtle" onClick={() => setRemoving(new Map())}>
                Clear removals
              </Button>
            )}
          </Group>
        )}
        {s.can_propose && s.pending && (
          <Text size="sm" mt="xs" c="dimmed">
            A change is already pending; a new one can be proposed once it is decided.
          </Text>
        )}
      </Card>

      {s.pending && (
        <PendingVersion
          module={module}
          pending={s.pending}
          categories={categories}
          canDecide={s.can_decide}
          onDecide={setDecision}
        />
      )}

      <Stack gap="xs" component="section" aria-labelledby="mapping-entries-title">
        <Title order={2} size="h4" id="mapping-entries-title">
          Entries of v{shownVersion?.version ?? '-'}
          {shownVersion && shownVersion.status !== 'ACTIVE' ? ` (${shownVersion.status.toLowerCase()})` : ''}
        </Title>
        <Group gap="xs" align="flex-end" wrap="wrap">
          <Select
            label="Category"
            data={[{ value: '', label: 'Any' }, ...categories]}
            value={category}
            onChange={(v) => {
              setCategory(v ?? '');
              setEntryPage(1);
            }}
            allowDeselect={false}
            searchable
            w={320}
          />
          <Select
            label="Match type"
            data={[{ value: '', label: 'Any' }, ...matchTypes]}
            value={matchType}
            onChange={(v) => {
              setMatchType(v ?? '');
              setEntryPage(1);
            }}
            allowDeselect={false}
            w={240}
          />
          <form onSubmit={applySearch}>
            <Group gap={4} align="flex-end">
              <TextInput
                label="Pattern contains"
                value={searchDraft}
                onChange={(e) => setSearchDraft(e.currentTarget.value)}
                w={220}
              />
              <Button type="submit" variant="default">
                Search entries
              </Button>
            </Group>
          </form>
          {!viewingActive && s.active && (
            <Button variant="subtle" onClick={() => setSelected(null)}>
              Back to the active version
            </Button>
          )}
        </Group>
        {canRemove && (
          <Text size="sm" c="dimmed">
            Tick entries to remove them in your proposal ({removing.size} ticked).
          </Text>
        )}
        {entries.error && (
          <Alert color="red" role="alert">
            {entries.error.message}
          </Alert>
        )}
        <Table.ScrollContainer minWidth={900}>
          <Table withTableBorder aria-label="NTSL mapping entries" aria-busy={entries.isFetching}>
            <Table.Thead>
              <Table.Tr>
                {canRemove && <Table.Th scope="col">Remove</Table.Th>}
                {['Entry', 'Match type', 'Pattern', 'Category', 'Txn type', 'Priority', 'Notes'].map((h) => (
                  <Table.Th key={h} scope="col">
                    {h}
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(entries.data?.results ?? []).map((e) => (
                <Table.Tr key={e.id}>
                  {canRemove && (
                    <Table.Td>
                      <Checkbox
                        aria-label={`Remove entry #${e.id}`}
                        checked={removing.has(e.id)}
                        onChange={(ev) => toggle(e, ev.currentTarget.checked)}
                      />
                    </Table.Td>
                  )}
                  <Table.Td>#{e.id}</Table.Td>
                  <Table.Td>{e.match_type}</Table.Td>
                  <Table.Td className="wrap-anywhere">
                    <code>{e.pattern}</code>
                  </Table.Td>
                  <Table.Td>{e.category_label}</Table.Td>
                  <Table.Td>{e.txn_type || '-'}</Table.Td>
                  <Table.Td>{e.priority}</Table.Td>
                  <Table.Td className="wrap-anywhere">{e.notes || '-'}</Table.Td>
                </Table.Tr>
              ))}
              {!entries.isPending && (entries.data?.results.length ?? 0) === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={canRemove ? 8 : 7}>No entries match.</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        <ListPager total={entryBounds.pages} value={entryPage} onChange={setEntryPage} label="Entries" />
      </Stack>

      <Stack gap="xs" component="section" aria-labelledby="mapping-versions-title">
        <Title order={2} size="h4" id="mapping-versions-title">
          Mapping history
        </Title>
        <Table.ScrollContainer minWidth={1000}>
          <Table withTableBorder aria-label="NTSL mapping versions">
            <Table.Thead>
              <Table.Tr>
                {['Version', 'Status', 'Entries', 'Changes', 'Reason', 'Proposed', 'Decided', ''].map(
                  (h, i) => (
                    <Table.Th key={i} scope="col">
                      {h}
                    </Table.Th>
                  ),
                )}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(versions.data?.results ?? []).map((v) => (
                <Table.Tr key={v.id}>
                  <Table.Td>v{v.version}</Table.Td>
                  <Table.Td>
                    <VersionStatusBadge value={v.status} /> {v.self_approved && <SelfApprovedBadge />}
                  </Table.Td>
                  <Table.Td ta="right">{formatCount(v.entry_count)}</Table.Td>
                  <Table.Td>
                    +{v.change_counts.added} / -{v.change_counts.removed}
                  </Table.Td>
                  <Table.Td className="wrap-anywhere">{v.reason || '-'}</Table.Td>
                  <Table.Td>
                    {v.proposed_by ?? '-'}
                    <Text size="xs" c="dimmed">
                      {formatDateTime(v.proposed_at)}
                    </Text>
                  </Table.Td>
                  <Table.Td className="wrap-anywhere">
                    {v.decided_by ?? '-'}
                    <Text size="xs" c="dimmed">
                      {formatDateTime(v.decided_at)}
                      {v.decision_remarks ? ` - ${v.decision_remarks}` : ''}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Button
                      size="xs"
                      variant="default"
                      disabled={v.id === versionId}
                      onClick={() => {
                        setSelected(v.id);
                        setEntryPage(1);
                      }}
                    >
                      View entries of v{v.version}
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        <ListPager
          total={versionBounds.pages}
          value={versionPage}
          onChange={setVersionPage}
          label="Versions"
        />
      </Stack>

      <Modal
        opened={proposing}
        onClose={() => setProposing(false)}
        title="Propose an NTSL mapping change"
        size="xl"
      >
        <MappingProposalForm
          categories={categories}
          matchTypes={matchTypes}
          txnTypes={txnTypes}
          removing={[...removing.values()]}
          onSubmit={propose}
          onCancel={() => setProposing(false)}
        />
      </Modal>
      <DecisionModal module={module} target={decision} onClose={() => setDecision(null)} />
    </Stack>
  );
}
