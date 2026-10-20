/**
 * One settlement result (design 4.14 / 4.15): status with the reasons, the
 * SOP check, tolerance, fees and the cycle x side table. Shared by the
 * settlement case detail and the per-date settlement screen.
 */
import { Group, List, Stack, Table, Text, Title } from '@mantine/core';

import type { SettlementResultBody } from '../../api/types';
import { SettlementStatusBadge } from '../../components/Badges';
import { displayValue, formatCount, formatDateTime, formatRupees, yesNo } from '../../lib/format';

export function reasonList(reasons: SettlementResultBody['reasons']): string[] {
  return Array.isArray(reasons)
    ? reasons.map(String)
    : Object.entries(reasons).map(([k, v]) => `${k}: ${displayValue(v)}`);
}

export function SettlementBlock({
  s,
  title = 'Settlement result',
}: {
  s: SettlementResultBody;
  title?: string;
}) {
  const reasons = reasonList(s.reasons);
  return (
    <Stack gap="xs">
      <Title order={2} size="h4">
        {title}
      </Title>
      <Group gap="xs">
        <SettlementStatusBadge value={s.status} />
        <Text size="sm">
          Computed {formatDateTime(s.computed_at)}
          {s.result_is_current ? '' : ' (no longer the current result)'}
        </Text>
      </Group>
      {reasons.length > 0 && (
        <List size="sm" aria-label="Settlement reasons">
          {reasons.map((r) => (
            <List.Item key={r}>{r}</List.Item>
          ))}
        </List>
      )}
      <Table withTableBorder aria-label="SOP check" maw={700}>
        <Table.Thead>
          <Table.Tr>
            {['CBS net', 'NTSL net', 'Difference', 'Explained', 'Unexplained'].map((h) => (
              <Table.Th key={h} scope="col" ta="right">
                {h}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          <Table.Tr>
            {[s.sop.cbs_net, s.sop.ntsl_net, s.sop.difference, s.sop.explained, s.sop.unexplained].map(
              (v, i) => (
                <Table.Td key={i} ta="right">
                  {formatRupees(v)}
                </Table.Td>
              ),
            )}
          </Table.Tr>
        </Table.Tbody>
      </Table>
      <Text size="sm">
        Tolerance: {formatRupees(s.tolerance.amount)} / {formatCount(s.tolerance.txn_count)} transactions.
        Fees net: {formatRupees(s.fees_net)}
      </Text>
      <Table.ScrollContainer minWidth={900}>
        <Table withTableBorder aria-label="Cycle by side">
          <Table.Thead>
            <Table.Tr>
              {['Cycle', 'Side', 'NTSL', 'Matched', 'Explained', 'Unexplained', 'Within tolerance'].map(
                (h) => (
                  <Table.Th key={h} scope="col">
                    {h}
                  </Table.Th>
                ),
              )}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {s.cycles.map((c, i) => (
              <Table.Tr key={`${c.cycle}-${c.side}-${i}`}>
                <Table.Td>{c.cycle ?? '-'}</Table.Td>
                <Table.Td>{c.side}</Table.Td>
                <Table.Td>
                  {formatRupees(c.ntsl_amount)} ({formatCount(c.ntsl_count)})
                </Table.Td>
                <Table.Td>
                  {formatRupees(c.matched_amount)} ({formatCount(c.matched_count)})
                </Table.Td>
                <Table.Td>
                  {formatRupees(c.explained_amount)} ({formatCount(c.explained_count)})
                </Table.Td>
                <Table.Td>
                  {formatRupees(c.unexplained_amount)} ({formatCount(c.unexplained_count)})
                </Table.Td>
                <Table.Td>{yesNo(c.within_tolerance)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
  );
}
