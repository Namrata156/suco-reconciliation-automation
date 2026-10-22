/**
 * My alerts (design 4.18, Slice 4 contract A): the signed-in user's own
 * alerts, newest first, paged on the server. "Unread only" is kept in the
 * address. Opening an alert's link marks it read; "Mark all read" marks only
 * the alerts up to the newest one shown, so one that arrived since stays
 * unread. The body is shown as plain text and never parsed for links
 * (older alerts mailed Django-admin URLs).
 */
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Select,
  Spoiler,
  Stack,
  Table,
  Text,
  Title,
  VisuallyHidden,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useEffect, useRef } from 'react';
import { Link } from 'react-router';

import type { UserAlert } from '../api/types';
import { ListPager } from '../components/ListPager';
import { useAlerts, useMarkAlertsRead } from '../hooks/alerts';
import { formatCount, formatDateTime } from '../lib/format';
import { spaLink } from '../lib/links';
import { pageBounds, useUrlFilters } from '../lib/urlFilters';

export const ALERTS_PAGE_SIZE = 50;
const FILTER_KEYS = ['unread'] as const;

const SHOW = [
  { value: '', label: 'All alerts' },
  { value: 'true', label: 'Unread only' },
];

export function AlertsPage() {
  const { filters, setFilter } = useUrlFilters(FILTER_KEYS, ALERTS_PAGE_SIZE);
  const query = useAlerts(filters);
  const { one, all } = useMarkAlertsRead();
  const data = query.data;
  const rows = data?.results ?? [];
  const { pages, first, last } = pageBounds(data?.count ?? 0, filters.page, ALERTS_PAGE_SIZE);

  // The newest alert this screen has shown (any page), for "mark all read".
  const newestShown = useRef(0);
  const newestOnPage = rows.reduce((m, a) => Math.max(m, a.id), 0);
  useEffect(() => {
    newestShown.current = Math.max(newestShown.current, newestOnPage);
  }, [newestOnPage]);

  const markAll = () => {
    all.mutate(newestShown.current || undefined, {
      onSuccess: (r) =>
        notifications.show({
          color: 'green',
          title: 'Alerts marked read',
          message: `${formatCount(r.marked)} marked read; ${formatCount(r.unread)} still unread.`,
        }),
      onError: (e) => notifications.show({ color: 'red', title: 'Not marked read', message: e.message }),
    });
  };
  const open = (a: UserAlert) => {
    if (!a.read) one.mutate(a.id);
  };

  return (
    <Stack gap="sm">
      <Title order={1} size="h3">
        Alerts
      </Title>
      <Group gap="xs" align="flex-end" wrap="wrap">
        <Select
          label="Show"
          data={SHOW}
          value={filters.unread ?? ''}
          onChange={(v) => setFilter('unread', v)}
          allowDeselect={false}
          w={180}
        />
        <Button
          variant="default"
          onClick={markAll}
          loading={all.isPending}
          disabled={!data || data.unread === 0}
        >
          Mark all read
        </Button>
      </Group>

      {query.error && (
        <Alert color="red" role="alert" title="The alerts could not be loaded">
          {query.error.message}
        </Alert>
      )}
      <Text size="sm" aria-live="polite">
        {query.isPending
          ? 'Loading...'
          : `${formatCount(data?.count ?? 0)} alert(s), ${formatCount(data?.unread ?? 0)} unread` +
            (rows.length ? `; showing ${formatCount(first)}-${formatCount(last)}` : '')}
      </Text>

      <Table.ScrollContainer minWidth={900}>
        <Table withTableBorder aria-label="My alerts" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {['Received', 'Type', 'Alert', 'Actions'].map((h) => (
                <Table.Th key={h} scope="col">
                  {h}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((a) => {
              const to = spaLink(a.link);
              return (
                <Table.Tr key={a.id} fw={a.read ? undefined : 600}>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>
                    {formatDateTime(a.created_at)}{' '}
                    {!a.read && (
                      <Badge size="sm" color="red" variant="light">
                        Unread
                      </Badge>
                    )}
                  </Table.Td>
                  <Table.Td>{a.alert_type_label}</Table.Td>
                  <Table.Td className="wrap-anywhere">
                    {to ? (
                      <Anchor
                        component={Link}
                        to={to}
                        onClick={() => open(a)}
                        aria-label={`${a.subject} (open alert ${a.id})`}
                      >
                        {a.subject}
                      </Anchor>
                    ) : (
                      <Text span>{a.subject}</Text>
                    )}
                    {a.body && (
                      <Spoiler
                        maxHeight={44}
                        // Unique names: one control per alert.
                        showLabel={
                          <>
                            Show more<VisuallyHidden> of alert {a.id}</VisuallyHidden>
                          </>
                        }
                        hideLabel={
                          <>
                            Show less<VisuallyHidden> of alert {a.id}</VisuallyHidden>
                          </>
                        }
                      >
                        <Text size="sm" c="dimmed" fw={400} style={{ whiteSpace: 'pre-wrap' }}>
                          {a.body}
                        </Text>
                      </Spoiler>
                    )}
                  </Table.Td>
                  <Table.Td>
                    {a.read ? (
                      <Text size="xs" c="dimmed" fw={400}>
                        Read {formatDateTime(a.read_at)}
                      </Text>
                    ) : (
                      <Button
                        size="xs"
                        variant="default"
                        onClick={() => one.mutate(a.id)}
                        loading={one.isPending && one.variables === a.id}
                        aria-label={`Mark alert ${a.id} read`}
                      >
                        Mark read
                      </Button>
                    )}
                  </Table.Td>
                </Table.Tr>
              );
            })}
            {!query.isPending && rows.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={4}>{filters.unread ? 'No unread alerts.' : 'No alerts yet.'}</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ListPager
        total={pages}
        value={filters.page}
        onChange={(p) => setFilter('page', String(p))}
        label="Alerts"
      />
    </Stack>
  );
}
