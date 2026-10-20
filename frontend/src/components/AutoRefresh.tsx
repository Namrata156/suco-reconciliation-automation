/**
 * The auto-refresh line on screens that wait on the server, with a manual
 * "Refresh" button that always works (see lib/polling.ts for why automatic
 * refresh pauses when the user is idle).
 */
import { Button, Group, Text } from '@mantine/core';

import type { Polling } from '../lib/polling';
import { POLL } from '../lib/polling';

export function AutoRefresh({
  polling,
  what,
  onRefresh,
  busy,
}: {
  polling: Polling;
  /** "files are being processed", "the run is in progress" ... */
  what: string | null;
  onRefresh: () => void;
  busy?: boolean;
}) {
  let text = '';
  if (polling.paused) text = `Automatic refresh paused while you are inactive (${what}).`;
  else if (polling.interval !== false) text = `Refreshing every ${POLL.everyMs / 1000} seconds while ${what}.`;
  return (
    <Group gap="xs" wrap="wrap">
      <Button size="xs" variant="default" onClick={onRefresh} loading={busy}>
        Refresh
      </Button>
      {text && (
        <Text size="sm" c="dimmed" role="status">
          {text}
        </Text>
      )}
    </Group>
  );
}
