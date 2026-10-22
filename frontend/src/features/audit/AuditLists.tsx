/**
 * The four read-only audit lists (design 4.19 / 3.6, Slice 4 contract B):
 * case events, settings events, user activity (login log) and operations.
 * They grow for ever, so they are paged and filtered on the server; every
 * filter is kept in the address (lib/urlFilters.ts), so a link reproduces
 * the list. Nothing on these screens changes anything: there are no
 * buttons other than filters, paging and links to the items.
 */
import {
  Alert,
  Anchor,
  Button,
  Group,
  MultiSelect,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState, type FormEvent, type ReactNode } from 'react';
import { Link } from 'react-router';

import { ApiError, getAuditMeta, listAudit, type AuditList, type AuditRows } from '../../api/client';
import type { AuditFilters, AuditMeta, Choice } from '../../api/types';
import { SelfApprovedBadge } from '../../components/Badges';
import { ListPager } from '../../components/ListPager';
import { displayValue, formatCount, formatDate, formatDateTime, todayIst } from '../../lib/format';
import { spaLink } from '../../lib/links';
import { pageBounds, useUrlFilters } from '../../lib/urlFilters';

export const AUDIT_PAGE_SIZE = 50;

// Per list: the filters the contract accepts (B1-B4). Module-level constants
// (useUrlFilters memo dependency).
const COMMON = ['date_from', 'date_to', 'user', 'event_type', 'ordering'] as const;
const CASE_KEYS = [...COMMON, 'module', 'business_date', 'case', 'rrn'] as const;
const SETTINGS_KEYS = [...COMMON, 'module', 'kind'] as const;
const ACTIVITY_KEYS = [...COMMON] as const;
const OPERATION_KEYS = [...COMMON, 'module', 'business_date'] as const;
type Key = (typeof CASE_KEYS)[number] | 'kind';

export function useAuditMeta() {
  return useQuery({ queryKey: ['audit', 'meta'], queryFn: getAuditMeta, staleTime: 10 * 60_000 });
}

/** {field: [old, new]} or any object as "field: old -> new; other: value". */
export function describeObject(obj: Record<string, unknown> | null | undefined): string {
  if (!obj) return '';
  return Object.entries(obj)
    .map(([k, v]) =>
      Array.isArray(v) && v.length === 2
        ? `${k}: ${displayValue(v[0])} -> ${displayValue(v[1])}`
        : `${k}: ${displayValue(v)}`,
    )
    .join('; ');
}

const ORDER_LABELS: Record<string, string> = {
  '-created_at': 'Newest first',
  created_at: 'Oldest first',
};

// ---------------------------------------------------------------------------
// filters
// ---------------------------------------------------------------------------
interface FilterSpec {
  keys: readonly Key[];
  eventTypes: (m: AuditMeta | undefined) => Choice[];
  /** Label of the event type filter (unique across the screen). */
  eventLabel: string;
}

/** Text filters are applied with Enter or "Apply", not on every key press. */
function TextFilters({
  fields,
  values,
  onApply,
}: {
  fields: { key: Key; label: string; w: number }[];
  values: Partial<Record<Key, string>>;
  onApply: (v: Partial<Record<Key, string>>) => void;
}) {
  const [draft, setDraft] = useState(values);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onApply(Object.fromEntries(fields.map((f) => [f.key, (draft[f.key] ?? '').trim()])));
  };
  return (
    <form onSubmit={submit} aria-label="Search filters">
      <Group gap="xs" align="flex-end" wrap="wrap">
        {fields.map((f) => (
          <TextInput
            key={f.key}
            label={f.label}
            value={draft[f.key] ?? ''}
            onChange={(e) => {
              // Read now: currentTarget is gone by the time the updater runs.
              const value = e.currentTarget.value;
              setDraft((d) => ({ ...d, [f.key]: value }));
            }}
            w={f.w}
          />
        ))}
        <Button type="submit" variant="default">
          Apply
        </Button>
      </Group>
    </form>
  );
}

function useAuditList<L extends AuditList>(list: L, keys: readonly Key[]) {
  const url = useUrlFilters(keys, AUDIT_PAGE_SIZE);
  const query = useQuery({
    queryKey: ['audit', list, url.filters],
    queryFn: () => listAudit(list, url.filters as AuditFilters),
    placeholderData: keepPreviousData,
  });
  return { ...url, query };
}

function AuditFiltersBar({
  spec,
  meta,
  filters,
  setFilter,
  setFilters,
  reset,
}: {
  spec: FilterSpec;
  meta: AuditMeta | undefined;
  filters: Partial<Record<Key, string>>;
  setFilter: (k: Key, v: string | null) => void;
  setFilters: (v: Partial<Record<Key, string | null>>) => void;
  reset: () => void;
}) {
  const has = (k: Key) => spec.keys.includes(k);
  const any = (list: Choice[]) => [{ value: '', label: 'Any' }, ...list];
  const textFields = [
    { key: 'user' as const, label: 'User name', w: 160 },
    ...(has('case') ? [{ key: 'case' as const, label: 'Case id', w: 110 }] : []),
    ...(has('rrn') ? [{ key: 'rrn' as const, label: 'RRN', w: 160 }] : []),
  ];
  const today = todayIst();
  return (
    <Stack gap="xs" component="section" aria-label="Filters">
      <Group gap="xs" align="flex-end" wrap="wrap">
        <DateInput
          label="From date"
          valueFormat="DD-MMM-YYYY"
          value={filters.date_from ?? null}
          onChange={(v) => setFilter('date_from', v)}
          clearable
          w={150}
        />
        <DateInput
          label="To date"
          valueFormat="DD-MMM-YYYY"
          value={filters.date_to ?? null}
          onChange={(v) => setFilter('date_to', v)}
          clearable
          w={150}
        />
        <Button variant="default" onClick={() => setFilters({ date_from: today, date_to: today })}>
          Today
        </Button>
        {has('module') && (
          <Select
            label="Payment module"
            data={any((meta?.modules ?? []).map((m) => ({ value: m.slug, label: m.code })))}
            value={filters.module ?? ''}
            onChange={(v) => setFilter('module', v)}
            allowDeselect={false}
            w={130}
          />
        )}
        {has('kind') && (
          <Select
            label="Kind of setting"
            data={any(meta?.settings_kinds ?? [])}
            value={filters.kind ?? ''}
            onChange={(v) => setFilter('kind', v)}
            allowDeselect={false}
            w={220}
          />
        )}
        <MultiSelect
          label={spec.eventLabel}
          placeholder={filters.event_type ? undefined : 'Any'}
          data={spec.eventTypes(meta)}
          value={filters.event_type ? filters.event_type.split(',') : []}
          onChange={(v) => setFilter('event_type', v.join(','))}
          searchable
          clearable
          w={280}
        />
        {has('business_date') && (
          <DateInput
            label="Business date"
            valueFormat="DD-MMM-YYYY"
            value={filters.business_date ?? null}
            onChange={(v) => setFilter('business_date', v)}
            clearable
            w={150}
          />
        )}
        <Select
          label="Order"
          data={(meta?.orderings ?? ['-created_at', 'created_at']).map((o) => ({
            value: o === '-created_at' ? '' : o,
            label: ORDER_LABELS[o] ?? o,
          }))}
          value={filters.ordering ?? ''}
          onChange={(v) => setFilter('ordering', v)}
          allowDeselect={false}
          w={150}
        />
      </Group>
      <Group gap="xs" align="flex-end" wrap="wrap">
        {/* Re-created when the address changes (reset, back button). */}
        <TextFilters
          key={textFields.map((f) => filters[f.key] ?? '').join('|')}
          fields={textFields}
          values={filters}
          onApply={setFilters}
        />
        <Button variant="subtle" onClick={reset}>
          Reset filters
        </Button>
      </Group>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// one list
// ---------------------------------------------------------------------------
function AuditTable<L extends AuditList>({
  list,
  spec,
  caption,
  headers,
  row,
}: {
  list: L;
  spec: FilterSpec;
  caption: string;
  headers: string[];
  row: (r: AuditRows[L]) => ReactNode;
}) {
  const { data: meta } = useAuditMeta();
  const { filters, setFilter, setFilters, reset, query } = useAuditList(list, spec.keys);
  const data = query.data;
  const { pages, first, last } = pageBounds(data?.count ?? 0, filters.page, AUDIT_PAGE_SIZE);
  const forbidden = query.error instanceof ApiError && query.error.isForbidden;

  return (
    <Stack gap="sm">
      <AuditFiltersBar
        spec={spec}
        meta={meta}
        filters={filters}
        setFilter={setFilter}
        setFilters={setFilters}
        reset={reset}
      />
      {query.error && (
        <Alert color="red" role="alert" title={`The ${caption.toLowerCase()} could not be loaded`}>
          {forbidden ? 'Your role may not read the audit trail.' : query.error.message}
        </Alert>
      )}
      <Text size="sm" aria-live="polite">
        {query.isPending
          ? 'Loading...'
          : `${formatCount(data?.count ?? 0)} event(s)` +
            (data?.results.length ? `; showing ${formatCount(first)}-${formatCount(last)}` : '')}
      </Text>
      <Table.ScrollContainer minWidth={1100}>
        <Table withTableBorder striped aria-label={caption} aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {headers.map((h) => (
                <Table.Th key={h} scope="col">
                  {h}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(data?.results ?? []).map(row)}
            {!query.isPending && (data?.results.length ?? 0) === 0 && (
              <Table.Tr>
                <Table.Td colSpan={headers.length}>No events match these filters.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ListPager
        total={pages}
        value={filters.page}
        onChange={(p) => setFilter('page', String(p))}
        label={caption}
      />
    </Stack>
  );
}

const by = (name: string | null | undefined) => name ?? 'System';
const When = ({ at }: { at: string }) => (
  <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateTime(at)}</Table.Td>
);

/** A link to the item, when the server's link is an SPA address. */
function ItemLink({ link, label, children }: { link: string | null; label: string; children: ReactNode }) {
  const to = spaLink(link);
  if (!to) return <>{children}</>;
  return (
    <Anchor component={Link} to={to} aria-label={label}>
      {children}
    </Anchor>
  );
}

// ---------------------------------------------------------------------------
// the four lists
// ---------------------------------------------------------------------------
const CASE_SPEC: FilterSpec = {
  keys: CASE_KEYS,
  eventTypes: (m) => m?.case_event_types ?? [],
  eventLabel: 'Case event type',
};

export function AuditCaseEvents() {
  return (
    <AuditTable
      list="case-events"
      spec={CASE_SPEC}
      caption="Case events"
      headers={['When', 'Case', 'Event', 'Status change', 'By', 'Details']}
      row={(e) => (
        <Table.Tr key={e.id}>
          <When at={e.created_at} />
          <Table.Td>
            <ItemLink link={e.link} label={`Case ${e.case_id} (event ${e.id})`}>
              Case {e.case_id}
            </ItemLink>
            <Text size="xs" c="dimmed">
              {e.module.toUpperCase()} {formatDate(e.business_date)}, RRN {e.rrn || '-'}
            </Text>
          </Table.Td>
          <Table.Td>
            {e.event_type_label} {e.self_approved && <SelfApprovedBadge />}
          </Table.Td>
          <Table.Td>
            {e.old_status || e.new_status ? `${e.old_status || '-'} -> ${e.new_status || '-'}` : '-'}
          </Table.Td>
          <Table.Td>
            {by(e.actor_username)}
            <Text size="xs" c="dimmed">
              {e.source}
            </Text>
          </Table.Td>
          <Table.Td className="wrap-anywhere">
            {e.remarks && <Text size="sm">{e.remarks}</Text>}
            {Object.keys(e.changes ?? {}).length > 0 && (
              <Text size="xs" c="dimmed">
                {describeObject(e.changes)}
              </Text>
            )}
            {e.action_snapshot && (
              <Text size="xs" c="dimmed">
                Action: {describeObject(e.action_snapshot)}
              </Text>
            )}
            {!e.remarks && !Object.keys(e.changes ?? {}).length && !e.action_snapshot && '-'}
          </Table.Td>
        </Table.Tr>
      )}
    />
  );
}

const SETTINGS_SPEC: FilterSpec = {
  keys: SETTINGS_KEYS,
  eventTypes: (m) => m?.settings_event_types ?? [],
  eventLabel: 'Settings event type',
};

export function AuditSettingsEvents() {
  return (
    <AuditTable
      list="settings-events"
      spec={SETTINGS_SPEC}
      caption="Settings events"
      headers={['When', 'Kind', 'Item', 'Event', 'By', 'Details']}
      row={(e) => (
        <Table.Tr key={e.id}>
          <When at={e.created_at} />
          <Table.Td>
            {e.kind_label}
            <Text size="xs" c="dimmed">
              {e.module.toUpperCase()}
            </Text>
          </Table.Td>
          <Table.Td className="wrap-anywhere">
            <ItemLink link={e.link} label={`${e.item_label} (settings event ${e.id})`}>
              {e.item_label}
            </ItemLink>
          </Table.Td>
          <Table.Td>
            {e.event_type_label} {e.self_approved && <SelfApprovedBadge />}
          </Table.Td>
          <Table.Td>{by(e.actor_username)}</Table.Td>
          <Table.Td className="wrap-anywhere">
            {e.remarks && <Text size="sm">{e.remarks}</Text>}
            {Object.keys(e.changes ?? {}).length > 0 && (
              <Text size="xs" c="dimmed">
                {describeObject(e.changes)}
              </Text>
            )}
            {!e.remarks && !Object.keys(e.changes ?? {}).length && '-'}
          </Table.Td>
        </Table.Tr>
      )}
    />
  );
}

const ACTIVITY_SPEC: FilterSpec = {
  keys: ACTIVITY_KEYS,
  eventTypes: (m) => m?.user_activity_types ?? [],
  eventLabel: 'Activity type',
};

export function AuditUserActivity() {
  return (
    <AuditTable
      list="user-activity"
      spec={ACTIVITY_SPEC}
      caption="Login log"
      headers={['When', 'Event', 'User', 'IP address', 'Browser and address']}
      row={(a) => (
        <Table.Tr key={a.id}>
          <When at={a.created_at} />
          <Table.Td>{a.event_type_label}</Table.Td>
          <Table.Td>
            {a.username || '-'}
            {a.user_id === null && (
              <Text size="xs" c="dimmed">
                no matching account
              </Text>
            )}
          </Table.Td>
          <Table.Td>{a.ip_address ?? '-'}</Table.Td>
          <Table.Td className="wrap-anywhere">
            <Text size="xs">{a.path || '-'}</Text>
            <Text size="xs" c="dimmed" lineClamp={1} title={a.user_agent}>
              {a.user_agent || '-'}
            </Text>
          </Table.Td>
        </Table.Tr>
      )}
    />
  );
}

const OPERATION_SPEC: FilterSpec = {
  keys: OPERATION_KEYS,
  eventTypes: (m) => m?.operation_types ?? [],
  eventLabel: 'Operation type',
};

export function AuditOperations() {
  return (
    <AuditTable
      list="operations"
      spec={OPERATION_SPEC}
      caption="Operations"
      headers={['When', 'Operation', 'By', 'File or date', 'Details']}
      row={(o) => {
        const target = o.uploaded_file_id
          ? `File ${o.uploaded_file_id}`
          : o.business_date
            ? formatDate(o.business_date)
            : '-';
        return (
          <Table.Tr key={o.id}>
            <When at={o.created_at} />
            <Table.Td>
              {o.event_type_label}
              <Text size="xs" c="dimmed">
                {o.module.toUpperCase()}, {o.source}
              </Text>
            </Table.Td>
            <Table.Td>{by(o.username)}</Table.Td>
            <Table.Td>
              {o.uploaded_file_id && o.business_date ? (
                <Text size="xs" c="dimmed">
                  {formatDate(o.business_date)}
                </Text>
              ) : null}
              <ItemLink link={o.link} label={`${target} (operation ${o.id})`}>
                {target}
              </ItemLink>
            </Table.Td>
            <Table.Td className="wrap-anywhere">
              <Text size="xs">{describeObject(o.details) || '-'}</Text>
            </Table.Td>
          </Table.Tr>
        );
      }}
    />
  );
}
