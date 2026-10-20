/**
 * NTSL self-check per cycle (design 4.14; settlement.selfcheck): PASS /
 * FAIL / MISSING with the four differences and any unmapped descriptions.
 */
import { Alert, List, Stack, Table, Text } from '@mantine/core';

import type { CheckFigure, SelfCheck } from '../../api/types';
import { CheckStatusBadge } from '../../components/Badges';
import { displayValue, formatAmount, formatCount } from '../../lib/format';

/** "12 of 12 cycles PASS" (the Slice 3 click-through checks this line). */
export function selfCheckLine(c: SelfCheck): string {
  if (c.cycles === 0) return 'No NTSL self-check for this date yet';
  const rest = [c.fail ? `${c.fail} FAIL` : '', c.missing ? `${c.missing} MISSING` : ''].filter(Boolean);
  return `${c.pass} of ${c.cycles} cycles PASS${rest.length ? ` (${rest.join(', ')})` : ''}`;
}

const diff = (f: CheckFigure) => formatAmount(f.difference);

function unmappedText(lines: unknown): string[] {
  if (!Array.isArray(lines)) return lines ? [displayValue(lines)] : [];
  return lines.map((l) => (typeof l === 'string' ? l : displayValue(l)));
}

export function SelfCheckTable({ check }: { check: SelfCheck }) {
  const unmapped = check.results.filter((r) => r.unmapped_count > 0);
  return (
    <Stack gap="xs">
      <Table.ScrollContainer minWidth={900}>
        <Table withTableBorder aria-label="NTSL self-check per cycle">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Cycle</Table.Th>
              <Table.Th scope="col">Status</Table.Th>
              <Table.Th scope="col" ta="right">
                NTSL lines
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Unmapped
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Sub-total debit diff
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Sub-total credit diff
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Settlement net diff
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Final net diff
              </Table.Th>
              <Table.Th scope="col">Mapping</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {check.results.map((r) => (
              <Table.Tr key={r.id}>
                <Table.Td>{r.cycle}</Table.Td>
                <Table.Td>
                  <CheckStatusBadge
                    value={r.status}
                    title={r.failures ? displayValue(r.failures) : undefined}
                  />
                </Table.Td>
                <Table.Td ta="right">{formatCount(r.line_count)}</Table.Td>
                <Table.Td ta="right">{formatCount(r.unmapped_count)}</Table.Td>
                <Table.Td ta="right">{diff(r.subtotal_debit)}</Table.Td>
                <Table.Td ta="right">{diff(r.subtotal_credit)}</Table.Td>
                <Table.Td ta="right">{diff(r.settlement_net)}</Table.Td>
                <Table.Td ta="right">{diff(r.final_net)}</Table.Td>
                <Table.Td>v{r.map_version}</Table.Td>
              </Table.Tr>
            ))}
            {check.results.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={9}>No NTSL lines checked for this date.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {unmapped.length > 0 && (
        <Alert color="orange" title="Unmapped NTSL descriptions">
          {unmapped.map((r) => (
            <div key={r.id}>
              <Text size="sm" fw={600}>
                Cycle {r.cycle}
              </Text>
              <List size="sm">
                {unmappedText(r.unmapped_lines).map((l, i) => (
                  <List.Item key={i}>{l}</List.Item>
                ))}
              </List>
            </div>
          ))}
        </Alert>
      )}
    </Stack>
  );
}
