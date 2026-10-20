/**
 * Settings (design 4.16): one area with three tabs, each its own address
 * (/<module>/settings/response-codes | ntsl-mapping | tolerance) so a link
 * or an alert can open a tab. All five roles read; Makers propose codes and
 * mapping, the Administrator proposes the tolerance, Checkers decide.
 *
 * The tabs are exported from here so the lazily loaded settings chunk holds
 * all of them.
 */
import { Stack, Tabs, Title } from '@mantine/core';
import { Outlet, useLocation, useNavigate } from 'react-router';

import { useModuleCode } from '../hooks/module';

export { NtslMappingTab } from '../features/settings/NtslMappingTab';
export { ResponseCodesTab } from '../features/settings/ResponseCodesTab';
export { ToleranceTab } from '../features/settings/ToleranceTab';

export const SETTINGS_TABS = [
  { value: 'response-codes', label: 'Response codes' },
  { value: 'ntsl-mapping', label: 'NTSL mapping' },
  { value: 'tolerance', label: 'Tolerance' },
] as const;

export function SettingsPage() {
  const module = useModuleCode();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const current = SETTINGS_TABS.find((t) => pathname.split('/').includes(t.value))?.value ?? 'response-codes';

  return (
    <Stack gap="sm">
      <Title order={1} size="h3">
        Settings
      </Title>
      <Tabs value={current} onChange={(v) => v && navigate(`/${module}/settings/${v}`)} keepMounted={false}>
        <Tabs.List aria-label="Settings areas">
          {SETTINGS_TABS.map((t) => (
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
