/**
 * Expected files of a business date (design 4.11): one row per file type,
 * one badge per expected cycle (or one for a daily file), with the slot's
 * state in words (never colour alone, 7.4). A slot holding a file links to it.
 */
import { Anchor, Group, Table, Text } from '@mantine/core';
import { Link } from 'react-router';

import type { ChecklistSlot } from '../../api/types';
import { FileStateBadge, fileStateLabel } from '../../components/Badges';

export interface ChecklistRow {
  key: string;
  label: string;
  slots: ChecklistSlot[];
}

/** Slots grouped by file type, in the server's order. */
export function groupSlots(slots: readonly ChecklistSlot[]): ChecklistRow[] {
  const rows: ChecklistRow[] = [];
  for (const s of slots) {
    const key = `${s.file_type_config_id}`;
    let row = rows.find((r) => r.key === key);
    if (!row) {
      row = { key, label: s.label, slots: [] };
      rows.push(row);
    }
    row.slots.push(s);
  }
  return rows;
}

export function Checklist({ module, slots }: { module: string; slots: ChecklistSlot[] }) {
  const rows = groupSlots(slots);
  return (
    <Table.ScrollContainer minWidth={900}>
      <Table aria-label="Expected files">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">File type</Table.Th>
            <Table.Th scope="col">Present</Table.Th>
            <Table.Th scope="col">Cycles</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => {
            const present = r.slots.filter((s) => s.state !== 'MISSING').length;
            return (
              <Table.Tr key={r.key}>
                <Table.Th scope="row" fw={500}>
                  {r.label}
                </Table.Th>
                <Table.Td>
                  {present} of {r.slots.length}
                </Table.Td>
                <Table.Td>
                  <Group gap={4} wrap="wrap">
                    {r.slots.map((s) => {
                      const name = `${r.label}${s.cycle ? ` ${s.cycle}` : ''}: ${fileStateLabel(s.state)}`;
                      const badge = (
                        <FileStateBadge
                          state={s.state}
                          prefix={s.cycle ?? 'daily'}
                          title={s.files.map((f) => f.filename).join(', ') || undefined}
                        />
                      );
                      const file = s.files[s.files.length - 1];
                      return file ? (
                        <Anchor
                          key={`${s.cycle}`}
                          component={Link}
                          to={`/${module}/files/${file.id}`}
                          aria-label={`${name} (${file.filename})`}
                        >
                          {badge}
                        </Anchor>
                      ) : (
                        <span key={`${s.cycle}`} aria-label={name} role="img">
                          {badge}
                        </span>
                      );
                    })}
                  </Group>
                </Table.Td>
              </Table.Tr>
            );
          })}
          {rows.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={3}>
                <Text size="sm">No file types are configured for this module.</Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
