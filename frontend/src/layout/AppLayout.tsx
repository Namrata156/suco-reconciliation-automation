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
 * The address of the same screen in another module (header switcher). A case
 * / file id or a single date would not carry over; the reports keep their
 * filters (business-date range), since the module is their only other filter.
 */
export function switchModuleHref(pathname: string, search: string, slug: string): string {
  const rest = pathname.split('/').slice(2);
  const first = rest[0] ?? '';
  if (['results', 'settlement', 'reports'].includes(first)) {
    const path = first === 'reports' ? rest.join('/') : first;
    return `/${slug}/${path}${search}`;
  }
  const section = ['cases', 'files', 'dates', 'settings', 'approvals'].includes(first) ? `/${first}` : '';
  return `/${slug}${section}`;
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
    navigate(switchModuleHref(location.pathname, location.search, me.modules.find((m) => m.code === code)?.slug ?? code.toLowerCase()));
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
            <img
              src={BRAND.logoIcon}
              alt={`${BRAND.bankName} logo`}
              width={32}
              height={32}
              style={{ display: 'block', borderRadius: 4, flexShrink: 0 }}
            />
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
