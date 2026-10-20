/**
 * CBS business-date selection (design 4.10, Amendment 1 step 2). Shown for a
 * file paused at AWAITING_DATE_SELECTION: the dates actually found in the
 * file, with the batch date pre-selected when it is one of them. The user
 * confirms; the server checks the date against the list, records who chose
 * it (core.OperationLog) and resumes processing. Operations User only; other
 * roles see the dates read-only.
 */
import { Alert, Button, Card, Group, Radio, Stack, Text, Title } from '@mantine/core';
import { useState } from 'react';

import { ApiError } from '../../api/client';
import type { DateSelectionInfo } from '../../api/types';
import { formatDate } from '../../lib/format';

export function DateSelection({
  selection,
  canSelect,
  onSelect,
}: {
  selection: DateSelectionInfo;
  canSelect: boolean;
  /** Throws an ApiError when the server refuses; the refusal is shown here. */
  onSelect: (isoDate: string) => Promise<void>;
}) {
  const [chosen, setChosen] = useState<string | null>(selection.preselected);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string[] | null>(null);
  const batch = selection.batch_business_date;

  const confirm = async () => {
    if (!chosen) return;
    setBusy(true);
    setRefusal(null);
    try {
      await onSelect(chosen);
    } catch (e) {
      setRefusal(
        e instanceof ApiError
          ? [e.detail, ...Object.values(e.fieldErrors).flat()]
          : ['The server could not be reached.'],
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card withBorder padding="md" component="section" aria-labelledby="date-selection-title">
      <Stack gap="sm">
        <Title order={2} size="h4" id="date-selection-title">
          Business date of this CBS file
        </Title>
        <Text size="sm">
          The file covers several dates. Choose the business date it is loaded for; only that date&apos;s rows
          are used.
        </Text>
        {batch && selection.preselected && (
          <Text size="sm">Pre-selected: {formatDate(batch)}, the date this file was uploaded for.</Text>
        )}
        {batch && !selection.preselected && (
          <Alert color="orange" title="The upload date is not in this file">
            The file was uploaded for {formatDate(batch)}, but that date is not among the dates found in it.
            Check that it is the right file before choosing.
          </Alert>
        )}
        <Radio.Group label="Dates found in the file" value={chosen} onChange={setChosen}>
          <Group mt="xs">
            {selection.detected_dates.map((d) => (
              <Radio key={d} value={d} label={formatDate(d)} disabled={!canSelect || busy} />
            ))}
          </Group>
        </Radio.Group>
        {refusal && (
          <Alert color="red" role="alert" title="Not accepted">
            {refusal.join(' ')}
          </Alert>
        )}
        {canSelect ? (
          <Group>
            <Button onClick={confirm} disabled={!chosen} loading={busy}>
              Confirm business date
            </Button>
          </Group>
        ) : (
          <Text size="sm" c="dimmed">
            An Operations User confirms the date.
          </Text>
        )}
      </Stack>
    </Card>
  );
}
