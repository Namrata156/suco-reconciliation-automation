/**
 * Dates whose current settlement used an older tolerance (owner 2026-10-20
 * Q4): nothing is re-run automatically after a tolerance approval. Each date
 * links to its overview, where an Operations User presses Run.
 */
import { Anchor, Stack, Table, Text } from '@mantine/core';
import { Link } from 'react-router';

import type { StaleToleranceDates } from '../../api/types';
import { SettlementStatusBadge } from '../../components/Badges';
import { formatCount, formatDate, formatDateTime, formatRupees } from '../../lib/format';

export function StaleDatesList({
  module,
  block,
  label = 'Dates settled with an older tolerance',
}: {
  module: string;
  block: StaleToleranceDates;
  label?: string;
}) {
  if (block.count === 0) {
    return <Text size="sm">Every date&apos;s settlement used the active tolerance.</Text>;
  }
  return (
    <Stack gap="xs">
      <Text size="sm">{block.note}</Text>
      <Table withTableBorder aria-label={label} maw={900}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Business date</Table.Th>
            <Table.Th scope="col">Settlement</Table.Th>
            <Table.Th scope="col">Tolerance used</Table.Th>
            <Table.Th scope="col">Computed</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {block.dates.map((d) => (
            <Table.Tr key={d.result_id}>
              <Table.Td>
                <Anchor
                  component={Link}
                  to={`/${module}/dates/${d.business_date}`}
                  aria-label={`Open ${formatDate(d.business_date)} to run it again`}
                >
                  {formatDate(d.business_date)}
                </Anchor>
              </Table.Td>
              <Table.Td>
                <SettlementStatusBadge value={d.settlement_status} />
              </Table.Td>
              <Table.Td>
                {d.tolerance_version ? `v${d.tolerance_version}: ` : 'none recorded: '}
                {formatRupees(d.tolerance.amount)} / {formatCount(d.tolerance.txn_count)} txn
              </Table.Td>
              <Table.Td>{formatDateTime(d.computed_at)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}
