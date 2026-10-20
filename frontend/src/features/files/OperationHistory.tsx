/**
 * The operations log of a file or a business date (core.OperationLog): who
 * uploaded, who chose the CBS date, who started a run (design 3.6,
 * Amendment 1).
 */
import { Table, Text } from '@mantine/core';

import type { OperationLogEntry } from '../../api/types';
import { formatDate, formatDateTime } from '../../lib/format';

/** A one-line summary of the entry's details. */
export function operationSummary(e: OperationLogEntry): string {
  const d = e.details ?? {};
  const str = (v: unknown) => (typeof v === 'string' && v ? v : null);
  switch (e.operation) {
    case 'FILE_UPLOADED':
      return [str(d.filename), str(d.file_type), str(d.cycle)].filter(Boolean).join(', ');
    case 'FILE_REFUSED': {
      const errors = Array.isArray(d.errors) ? d.errors.filter((x) => typeof x === 'string') : [];
      return [str(d.filename), ...errors].filter(Boolean).join(' - ');
    }
    case 'BUSINESS_DATE_SELECTED': {
      const found = Array.isArray(d.detected_dates) ? d.detected_dates.map((x) => formatDate(String(x))) : [];
      return `Chose ${formatDate(e.business_date)}${found.length ? ` from ${found.join(', ')}` : ''}`;
    }
    case 'RUN_STARTED':
      return e.run_id ? `Run #${e.run_id}` : '';
    default:
      return '';
  }
}

export function OperationHistory({ entries, label }: { entries: OperationLogEntry[]; label: string }) {
  if (entries.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Nothing recorded yet.
      </Text>
    );
  }
  return (
    <Table.ScrollContainer minWidth={700}>
      <Table aria-label={label}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">When</Table.Th>
            <Table.Th scope="col">What</Table.Th>
            <Table.Th scope="col">Who</Table.Th>
            <Table.Th scope="col">Details</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {entries.map((e) => (
            <Table.Tr key={e.id}>
              <Table.Td>{formatDateTime(e.created_at)}</Table.Td>
              <Table.Td>{e.operation_label}</Table.Td>
              <Table.Td>
                {e.username || '-'}
                {e.source ? ` (${e.source})` : ''}
              </Table.Td>
              <Table.Td className="wrap-anywhere">{operationSummary(e) || '-'}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
