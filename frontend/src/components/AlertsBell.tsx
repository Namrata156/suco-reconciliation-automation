/**
 * The header alerts bell (design 4.2 / 4.18): the unread count, opening a
 * small menu with "See all alerts" (the alerts list; since UI refinements
 * 2026-10-27, 9.3, the bell is the only way in - the menu item is gone). The
 * count is polled only while the user is active (see hooks/alerts.ts), so the
 * bell never keeps an idle session open.
 *
 * New-alert notice (owner 2026-10-22): the first response only records the
 * newest unread alert's id; when a later poll brings a higher one, a "New
 * alert" notification offers to open the list. No extra requests: it reads
 * the same poll.
 */
import { ActionIcon, Button, Indicator, Menu, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useEffect, useRef } from 'react';
import { Link, useNavigate } from 'react-router';

import { isNewAlert, useUnreadAlertCount } from '../hooks/alerts';

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

/** The first line of the bell's menu. */
export function unreadLine(unread: number | undefined): string {
  if (unread === undefined) return 'Alerts';
  if (unread === 0) return 'No unread alerts';
  return unread === 1 ? '1 unread alert' : `${unread} unread alerts`;
}

export function AlertsBell() {
  const { data } = useUnreadAlertCount();
  const navigate = useNavigate();
  const unread = data?.unread;
  const latestId = data === undefined ? undefined : data.latest_id;
  const shown = unread && unread > 99 ? '99+' : unread;

  // undefined: no response yet; then the highest latest_id seen (null: none had one).
  const seen = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    if (latestId === undefined) return;
    const before = seen.current;
    const isNew = before !== undefined && isNewAlert(before, latestId);
    // The first response is only recorded; a lower or null id later keeps the highest seen.
    if (before === undefined || isNew) seen.current = latestId;
    if (!isNew) return;
    // The notification is rendered outside the router, so it navigates through the bell's hook.
    const id = `new-alert-${latestId}`;
    notifications.show({
      id,
      title: 'New alert',
      autoClose: 10_000,
      message: (
        <>
          <Text size="sm">
            {unread === 1 ? 'You have 1 unread alert.' : `You have ${unread} unread alerts.`}
          </Text>
          <Button
            size="xs"
            variant="light"
            mt={6}
            onClick={() => {
              notifications.hide(id);
              navigate('/alerts');
            }}
          >
            Open alerts
          </Button>
        </>
      ),
    });
  }, [latestId, unread, navigate]);

  return (
    <Menu position="bottom-end" withinPortal>
      <Indicator label={shown} size={16} disabled={!unread} color="red" offset={4}>
        <Menu.Target>
          <ActionIcon variant="subtle" size="lg" aria-label={bellLabel(unread)}>
            <BellIcon />
          </ActionIcon>
        </Menu.Target>
      </Indicator>
      <Menu.Dropdown>
        <Menu.Label>{unreadLine(unread)}</Menu.Label>
        <Menu.Item component={Link} to="/alerts">
          See all alerts
        </Menu.Item>
      </Menu.Dropdown>
    </Menu>
  );
}
