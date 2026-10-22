/**
 * The header alerts bell (design 4.2 / 4.18): the unread count, linking to
 * the alerts list. The count is polled only while the user is active (see
 * hooks/alerts.ts), so the bell never keeps an idle session open.
 */
import { ActionIcon, Indicator } from '@mantine/core';
import { Link } from 'react-router';

import { useUnreadAlertCount } from '../hooks/alerts';

function BellIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden="true"
    >
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  );
}

/** "Alerts, 3 unread" - the name says the count, as the badge is visual only. */
export function bellLabel(unread: number | undefined): string {
  if (unread === undefined) return 'Alerts';
  if (unread === 0) return 'Alerts, none unread';
  return `Alerts, ${unread} unread`;
}

export function AlertsBell() {
  const { data: unread } = useUnreadAlertCount();
  const shown = unread && unread > 99 ? '99+' : unread;
  return (
    <Indicator label={shown} size={16} disabled={!unread} color="red" offset={4}>
      <ActionIcon component={Link} to="/alerts" variant="subtle" size="lg" aria-label={bellLabel(unread)}>
        <BellIcon />
      </ActionIcon>
    </Indicator>
  );
}
