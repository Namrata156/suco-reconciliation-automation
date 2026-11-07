/**
 * Review step of the upload (UI refinements U4, 2.1; contract E1): the date's
 * expected files against this batch - already there, filled by this batch,
 * still missing after it - and the batch files that fill no expected slot.
 * The server matches on the PROPOSED type and cycle; a type corrected in the
 * table is checked again when the file is uploaded. The full list of
 * expected files is collapsed; what is missing is always in view.
 */
import { Alert, Badge, List, Stack, Table, Text, Title } from '@mantine/core';

import type { DetectChecklist, DetectChecklistSlot } from '../../api/types';
import { DetailsSection } from '../../components/DetailsSection';
import { formatCount, formatDate } from '../../lib/format';

const GROUP: Record<string, { label: string; color: string }> = {
  ALREADY_PRESENT: { label: 'Already here', color: 'gray' },
  IN_BATCH: { label: 'In this batch', color: 'blue' },
  MISSING: { label: 'Still missing', color: 'orange' },
};

const slotName = (s: Pick<DetectChecklistSlot, 'label' | 'cycle'>) =>
  s.cycle ? `${s.label} ${s.cycle}` : s.label;

/** "60 expected: 41 already here, 17 in this batch, 2 still missing after this upload." */
export function checklistLine(c: DetectChecklist): string {
  const s = c.summary;
  return (
    `${formatCount(s.expected)} expected: ${formatCount(s.already_present)} already here, ` +
    `${formatCount(s.in_batch)} in this batch, ${formatCount(s.missing_after_batch)} still missing after this upload.`
  );
}

export function BatchChecklist({
  checklist,
  fileName,
  busy,
}: {
  checklist: DetectChecklist;
  /** The batch file's name by its index. */
  fileName: (index: number) => string;
  /** The checklist is being read again for another date. */
  busy: boolean;
}) {
  const missing = checklist.slots.filter((s) => s.group === 'MISSING');
  // A batch file for a slot that already has a current file: the upload will refuse it.
  const clashes = checklist.slots.filter((s) => s.group === 'ALREADY_PRESENT' && s.batch_files.length > 0);
  return (
    <Stack gap="xs" component="section" aria-labelledby="batch-checklist-title" aria-busy={busy}>
      <Title order={2} size="h4" id="batch-checklist-title">
        Expected files for {formatDate(checklist.business_date)}
      </Title>
      <Text size="sm" fw={600} aria-live="polite">
        {busy ? 'Checking the expected files for this date...' : checklistLine(checklist)}
      </Text>
      {missing.length > 0 && (
        <Alert color="orange" title={`Still missing after this upload: ${formatCount(missing.length)}`}>
          <List size="sm">
            {missing.map((s) => (
              <List.Item key={`${s.file_type_config_id}-${s.cycle ?? ''}`}>{slotName(s)}</List.Item>
            ))}
          </List>
        </Alert>
      )}
      {clashes.length > 0 && (
        <Alert color="red" title="Already loaded: these files will be refused">
          <List size="sm">
            {clashes.map((s) => (
              <List.Item key={`${s.file_type_config_id}-${s.cycle ?? ''}`} className="wrap-anywhere">
                {slotName(s)} is already there ({s.existing_files.map((f) => f.filename).join(', ')});{' '}
                {s.batch_files.map(fileName).join(', ')} would be refused.
              </List.Item>
            ))}
          </List>
        </Alert>
      )}
      {checklist.outside_checklist.length > 0 && (
        <Text size="sm">
          {formatCount(checklist.outside_checklist.length)} file(s) of this batch fill no expected file of the
          date (type not recognised, no cycle, another date in the name, or not expected):{' '}
          {checklist.outside_checklist.map(fileName).join(', ')}.
        </Text>
      )}
      <Text size="xs" c="dimmed">
        Matched on the proposed type and cycle; a type you change below is checked again when the file is
        uploaded.
      </Text>
      <DetailsSection
        title={`All expected files (${formatCount(checklist.slots.length)})`}
        id="batch-slots-title"
        order={3}
      >
        <Table.ScrollContainer minWidth={700}>
          <Table withTableBorder aria-label={`Expected files for ${formatDate(checklist.business_date)}`}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Expected file</Table.Th>
                <Table.Th scope="col">Status</Table.Th>
                <Table.Th scope="col">File(s)</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {checklist.slots.map((s) => {
                const g = GROUP[s.group] ?? { label: s.group, color: 'gray' };
                const names = [
                  ...s.existing_files.map((f) => f.filename),
                  ...s.batch_files.map((i) => `${fileName(i)} (this batch)`),
                ];
                return (
                  <Table.Tr key={`${s.file_type_config_id}-${s.cycle ?? ''}`}>
                    <Table.Td>{slotName(s)}</Table.Td>
                    <Table.Td>
                      <Badge color={g.color} variant="light">
                        {g.label}
                      </Badge>
                    </Table.Td>
                    <Table.Td className="wrap-anywhere">
                      <Text size="sm">{names.join(', ') || '-'}</Text>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </DetailsSection>
    </Stack>
  );
}
