/**
 * The contributing transactions behind a settlement result (design 4.15):
 * one kind at a time (npci / sop / adjustments / z), filtered and paged on
 * the server. The row fields differ per kind; the server names them
 * ("fields"), so the table is built from that list.
 *
 * Used on the per-date settlement screen (the date's current result) and on
 * a settlement case (the result the case is about).
 */
import { Alert, Button, Group, SegmentedControl, Select, Stack, Table, Text, TextInput } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { ApiError, listSettlementTransactions, type SettlementSource } from '../../api/client';
import type { ModuleMeta, SettlementTransactionFilters, SettlementTransactionKind } from '../../api/types';
import { ListPager } from '../../components/ListPager';
import { displayValue, formatAmount, formatCount, isAmountString } from '../../lib/format';
import { pageBounds } from '../../lib/urlFilters';

export const TX_PAGE_SIZE = 50;

const KINDS: { value: SettlementTransactionKind; short: string }[] = [
  { value: 'npci', short: 'NPCI rows (G02-G06)' },
  { value: 'sop', short: 'SOP check items' },
  { value: 'adjustments', short: 'Adjustments (G09)' },
  { value: 'z', short: 'No NTSL money (Z)' },
];

/** Text filters the server applies when the kind has that field. */
const TEXT_FILTERS = [
  { name: 'rrn', label: 'RRN' },
  { name: 'rule', label: 'Rule' },
  { name: 'g_rule', label: 'G rule' },
] as const;

type Draft = { rrn: string; rule: string; g_rule: string; cycle: string; side: string };
const EMPTY: Draft = { rrn: '', rule: '', g_rule: '', cycle: '', side: '' };

const HEADER: Record<string, string> = {
  rrn: 'RRN',
  g_rule: 'G rule',
  rule: 'Rule',
  cycle: 'Cycle',
  side: 'Side',
  amount: 'Amount',
  npci_code: 'NPCI code',
  role: 'Role',
  adjtype: 'Adjustment type',
  remitter: 'Remitter',
  beneficiary: 'Beneficiary',
  raised_at: 'Raised at',
  signed: 'Signed amount',
  legs: 'Legs',
};

export function SettlementTransactions({
  module,
  source,
  meta,
}: {
  module: string;
  source: SettlementSource;
  meta: ModuleMeta | undefined;
}) {
  const [kind, setKind] = useState<SettlementTransactionKind>('npci');
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [applied, setApplied] = useState<Draft>(EMPTY);
  const [page, setPage] = useState(1);

  const filters: SettlementTransactionFilters = {
    kind,
    page,
    page_size: TX_PAGE_SIZE,
    ...Object.fromEntries(Object.entries(applied).filter(([, v]) => v)),
  };
  const query = useQuery({
    queryKey: ['settlement-transactions', module, source, filters],
    queryFn: () => listSettlementTransactions(module, source, filters),
    placeholderData: keepPreviousData,
  });

  const data = query.data;
  const fields = data?.fields ?? [];
  const has = (name: string) => fields.length === 0 || fields.includes(name);
  const { pages, first, last } = pageBounds(data?.count ?? 0, page, TX_PAGE_SIZE);
  const kindLabel = meta?.settlement_transaction_kinds.find((k) => k.value === kind)?.label;

  const apply = (e: FormEvent) => {
    e.preventDefault();
    setApplied({ ...draft, rrn: draft.rrn.trim(), rule: draft.rule.trim(), g_rule: draft.g_rule.trim() });
    setPage(1);
  };
  const clear = () => {
    setDraft(EMPTY);
    setApplied(EMPTY);
    setPage(1);
  };

  if (query.error instanceof ApiError && query.error.status === 404) {
    return <Text size="sm">{query.error.detail}</Text>;
  }

  return (
    <Stack gap="xs">
      <SegmentedControl
        aria-label="Kind of contributing transactions"
        value={kind}
        onChange={(v) => {
          setKind(v as SettlementTransactionKind);
          setPage(1);
        }}
        data={KINDS.map((k) => ({
          value: k.value,
          label: data ? `${k.short} (${formatCount(data.counts[k.value])})` : k.short,
        }))}
      />
      {kindLabel && (
        <Text size="sm" c="dimmed">
          {kindLabel}
        </Text>
      )}
      <form onSubmit={apply} aria-label="Transaction filters">
        <Group gap="xs" align="flex-end" wrap="wrap">
          {TEXT_FILTERS.filter((f) => has(f.name)).map((f) => (
            <TextInput
              key={f.name}
              label={f.label}
              aria-label={`Filter transactions by ${f.label}`}
              value={draft[f.name]}
              onChange={(e) => {
                const value = e.currentTarget.value;
                setDraft((d) => ({ ...d, [f.name]: value }));
              }}
              w={150}
            />
          ))}
          {has('cycle') && (
            <Select
              label="Cycle"
              aria-label="Filter transactions by cycle"
              data={[
                { value: '', label: 'Any' },
                ...(meta?.settlement_cycles ?? []).map((c) => ({ value: c.code, label: c.code })),
              ]}
              value={draft.cycle}
              onChange={(v) => setDraft((d) => ({ ...d, cycle: v ?? '' }))}
              allowDeselect={false}
              w={100}
            />
          )}
          {has('side') && (
            <Select
              label="Side"
              aria-label="Filter transactions by side"
              data={[{ value: '', label: 'Any' }, ...(meta?.settlement_sides ?? [])]}
              value={draft.side}
              onChange={(v) => setDraft((d) => ({ ...d, side: v ?? '' }))}
              allowDeselect={false}
              w={220}
            />
          )}
          <Button type="submit" variant="default">
            Apply transaction filters
          </Button>
          <Button variant="subtle" onClick={clear}>
            Clear transaction filters
          </Button>
        </Group>
      </form>

      {query.error && (
        <Alert color="red" role="alert" title="The transactions could not be loaded">
          {query.error.message}
        </Alert>
      )}
      <Text size="sm" aria-live="polite">
        {query.isPending
          ? 'Loading transactions...'
          : `Showing ${formatCount(first)}-${formatCount(last)} of ${formatCount(data?.count ?? 0)} transactions`}
      </Text>
      <Table.ScrollContainer minWidth={900}>
        <Table withTableBorder aria-label="Contributing transactions" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {fields.map((f) => (
                <Table.Th key={f} scope="col">
                  {HEADER[f] ?? f}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(data?.results ?? []).map((row, i) => (
              <Table.Tr key={`${String(row['rrn'] ?? '')}-${i}`}>
                {fields.map((f) => {
                  const v = row[f];
                  return isAmountString(v) ? (
                    <Table.Td key={f} ta="right">
                      {formatAmount(v)}
                    </Table.Td>
                  ) : (
                    <Table.Td key={f} className="wrap-anywhere">
                      {displayValue(v)}
                    </Table.Td>
                  );
                })}
              </Table.Tr>
            ))}
            {!query.isPending && (data?.results.length ?? 0) === 0 && (
              <Table.Tr>
                <Table.Td colSpan={Math.max(1, fields.length)}>No transactions of this kind.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ListPager total={pages} value={page} onChange={setPage} label="Transactions" />
    </Stack>
  );
}
