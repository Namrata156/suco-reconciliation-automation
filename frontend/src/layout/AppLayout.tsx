import {
  AppShell,
  Burger,
  Group,
  Menu,
  NavLink,
  Select,
  Skeleton,
  Text,
  UnstyledButton,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { Suspense, useEffect } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router';

import { logout } from '../api/client';
import { useMe, useResetSession } from '../auth/auth';
import { AlertsBell } from '../components/AlertsBell';
import { SessionWatcher } from '../auth/SessionWatcher';
import { BRAND } from '../config';
import { useModuleCode } from '../hooks/module';
import { can, menuFor, menuHref } from '../lib/roles';
import { NotFoundPage } from '../pages/NotFoundPage';

/**
 * True when the menu href matches the current address (path + mine filter);
 * with matchPrefix also for the addresses below it (the settings tabs).
 */
export function isActive(href: string, pathname: string, search: string, matchPrefix = false): boolean {
  const [path = '', query = ''] = href.split('?');
  const here = pathname.replace(/\/$/, '');
  if (path !== here && !(matchPrefix && here.startsWith(`${path}/`))) return false;
  const want = new URLSearchParams(query).get('mine') ?? '';
  const have = new URLSearchParams(search).get('mine') ?? '';
  return want === have;
}

/**
 * The module the module-less screens (alerts, audit, users) link back to:
 * the last one visited in this tab, kept in memory only (design 7.1).
 */
let lastModuleSlug = '';

/**
 * The app shell (design 4.2). `global` is for the screens that are not about
 * one payment module (alerts, audit, users; design 3.1): the header and the
 * menu use the last module visited, or the first one.
 */
export function AppLayout({ global = false }: { global?: boolean }) {
  const me = useMe();
  const urlModule = useModuleCode();
  // After a sign-in as someone else the remembered module may not be theirs.
  const module = global
    ? (me.modules.find((m) => m.slug === lastModuleSlug) ?? me.modules[0])?.slug ?? ''
    : urlModule;
  const location = useLocation();
  const navigate = useNavigate();
  const reset = useResetSession();
  const [opened, { toggle, close }] = useDisclosure();

  const current = me.modules.find((m) => m.slug === module);
  const currentSlug = current?.slug;
  useEffect(() => {
    if (!global && currentSlug) lastModuleSlug = currentSlug;
  }, [global, currentSlug]);
  if (!current) {
    if (global) return <NotFoundPage message="No payment module is available to your account." />;
    return <NotFoundPage message={`Unknown payment module "${module}".`} />;
  }

  const items = menuFor(me);

  const switchModule = (code: string | null) => {
    if (!code) return;
    // Same screen in the other module; a case / file id or date would not carry over.
    const rest = location.pathname.split('/').slice(2);
    const section = ['cases', 'files', 'dates', 'settings', 'approvals'].includes(rest[0] ?? '')
      ? `/${rest[0]}`
      : '';
    navigate(`/${me.modules.find((m) => m.code === code)?.slug ?? code.toLowerCase()}${section}`);
  };

  const signOut = async () => {
    try {
      await logout();
    } finally {
      reset();
      navigate('/login', { replace: true });
    }
  };

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 230, breakpoint: 'sm', collapsed: { mobile: !opened } }}
      padding="md"
    >
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label="Toggle menu" />
            <Text
              fw={700}
              size="sm"
              c="dimmed"
              aria-label="Bank logo placeholder"
              style={{ border: '1px dashed', padding: '2px 6px' }}
            >
              {BRAND.logoPlaceholder}
            </Text>
            <Text fw={700}>{BRAND.bankName}</Text>
            <Text c="dimmed" visibleFrom="md">
              {BRAND.appName}
            </Text>
          </Group>
          <Group gap="sm" wrap="nowrap">
            {/* Hidden while only one module exists (design 4.2). */}
            {me.modules.length > 1 ? (
              <Select
                aria-label="Payment module"
                data={me.modules.map((m) => ({ value: m.code, label: m.name || m.code }))}
                value={current.code}
                onChange={switchModule}
                allowDeselect={false}
                w={140}
              />
            ) : (
              <Text size="sm" fw={600} aria-label="Payment module">
                {current.code}
              </Text>
            )}
            {can(me.permissions, 'alerts.view') && <AlertsBell />}
            <Menu position="bottom-end" withinPortal>
              <Menu.Target>
                <UnstyledButton aria-label={`User menu for ${me.full_name || me.username}`} px="xs">
                  <Text size="sm" fw={600}>
                    {me.full_name || me.username}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {me.roles.join(', ')}
                  </Text>
                </UnstyledButton>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Label>Signed in as {me.username}</Menu.Label>
                <Menu.Item onClick={signOut}>Sign out</Menu.Item>
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="xs" component="nav" aria-label="Main menu">
        {items.map((item) => {
          const href = menuHref(item, current.slug);
          if (item.external) {
            return <NavLink key={item.key} label={item.label} href={href} component="a" />;
          }
          return (
            <NavLink
              key={item.key}
              label={item.label}
              component={Link}
              to={href}
              onClick={close}
              active={isActive(href, location.pathname, location.search, item.matchPrefix)}
            />
          );
        })}
      </AppShell.Navbar>

      <AppShell.Main id="main" tabIndex={-1} style={{ outline: 'none' }}>
        <Suspense fallback={<Skeleton h={300} aria-label="Loading the screen" />}>
          <Outlet />
        </Suspense>
      </AppShell.Main>
      <SessionWatcher />
    </AppShell>
  );
}
