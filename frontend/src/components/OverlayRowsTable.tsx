/**
 * DRC / Timeout / Adjustment rows for one RRN (design 4.5 case detail and
 * 4.13 match group detail; exception_management/detail.py _row).
 */
import { Table, Text } from '@mantine/core';

import type { OverlayRow } from '../api/types';
import { formatDate, formatRupees } from '../lib/format';

const HEADERS = [
  'Leg',
  'File date',
  'Amount',
  'Direction',
  'Response code',
  'SSK side',
  'Dispute type',
  'Txn date',
  'Note',
];

export function OverlayRowsTable({ rows, label = 'Dispute rows' }: { rows: OverlayRow[]; label?: string }) {
  if (rows.length === 0) return <Text size="sm">None.</Text>;
  return (
    <Table.ScrollContainer minWidth={900}>
      <Table withTableBorder aria-label={label}>
        <Table.Thead>
          <Table.Tr>
            {HEADERS.map((h) => (
              <Table.Th key={h} scope="col">
                {h}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={`${r.leg}-${r.fact_id}`}>
              <Table.Td>{r.leg}</Table.Td>
              <Table.Td>{formatDate(r.file_date)}</Table.Td>
              <Table.Td ta="right">{formatRupees(r.amount)}</Table.Td>
              <Table.Td>{r.direction || '-'}</Table.Td>
              <Table.Td>{r.response_code || '-'}</Table.Td>
              <Table.Td>{r.ssk_side || '-'}</Table.Td>
              <Table.Td>{r.dispute_type || '-'}</Table.Td>
              <Table.Td>{r.txn_date || '-'}</Table.Td>
              <Table.Td>
                {[
                  r.belongs_to_case_date ? '' : 'From another loaded date',
                  r.original_not_loaded ? 'Original transaction not loaded' : '',
                ]
                  .filter(Boolean)
                  .join('; ') || '-'}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
