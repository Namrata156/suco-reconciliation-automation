/**
 * Audit trail (design 4.19 / 3.6, Slice 4): four read-only lists, each its
 * own address (/audit/case-events | settings-events | user-activity |
 * operations) so a link or bookmark opens one with its filters. Not
 * module-scoped (contract B: module is a filter). Administrator and Auditor
 * only (/me "audit.view"; the server answers 403 to anyone else).
 *
 * The lists are exported from here so the lazily loaded audit chunk holds
 * all of them.
 */
import { Alert, Stack, Tabs, Text, Title } from '@mantine/core';
import { Outlet, useLocation, useNavigate } from 'react-router';

import { useMe } from '../auth/auth';
import { can } from '../lib/roles';

export {
  AuditCaseEvents,
  AuditOperations,
  AuditSettingsEvents,
  AuditUserActivity,
} from '../features/audit/AuditLists';

export const AUDIT_TABS = [
  { value: 'case-events', label: 'Case events' },
  { value: 'settings-events', label: 'Settings events' },
  { value: 'user-activity', label: 'Login log' },
  { value: 'operations', label: 'Operations' },
] as const;

export function AuditPage() {
  const me = useMe();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const current = AUDIT_TABS.find((t) => pathname.split('/').includes(t.value))?.value ?? 'case-events';

  if (!can(me.permissions, 'audit.view')) {
    return (
      <Alert color="yellow" title="Audit trail" role="alert">
        Only an Administrator or an Auditor reads the audit trail.
      </Alert>
    );
  }

  return (
    <Stack gap="sm">
      <Title order={1} size="h3">
        Audit trail
      </Title>
      <Text size="sm" c="dimmed">
        Read-only. Times are IST; the date filters apply to when the event was recorded.
      </Text>
      <Tabs value={current} onChange={(v) => v && navigate(`/audit/${v}`)} keepMounted={false}>
        <Tabs.List aria-label="Audit lists">
          {AUDIT_TABS.map((t) => (
            <Tabs.Tab key={t.value} value={t.value}>
              {t.label}
            </Tabs.Tab>
          ))}
        </Tabs.List>
      </Tabs>
      <Outlet />
    </Stack>
  );
}
