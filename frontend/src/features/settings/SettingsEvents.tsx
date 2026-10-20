/** The append-only event log of one settings proposal (who proposed / decided, when). */
import { List, Text } from '@mantine/core';

import type { SettingsEvent } from '../../api/types';
import { SelfApprovedBadge } from '../../components/Badges';
import { formatDateTime } from '../../lib/format';

export function SettingsEvents({ events, label }: { events: SettingsEvent[] | undefined; label: string }) {
  if (!events || events.length === 0) return null;
  return (
    <List size="sm" aria-label={label}>
      {events.map((e, i) => (
        <List.Item key={`${e.event_type}-${i}`}>
          <Text size="sm" span>
            {formatDateTime(e.created_at)} - {e.event_type} by {e.actor_username ?? 'system'}
            {e.remarks ? `: ${e.remarks}` : ''}
          </Text>{' '}
          {e.self_approved && <SelfApprovedBadge />}
        </List.Item>
      ))}
    </List>
  );
}
