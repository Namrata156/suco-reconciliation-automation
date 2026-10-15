import { AppShell, Burger, Group, Menu, NavLink, Select, Text, UnstyledButton } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { Link, Outlet, useLocation, useNavigate } from 'react-router';

import { logout } from '../api/client';
import { useMe, useResetSession } from '../auth/auth';
import { SessionWatcher } from '../auth/SessionWatcher';
import { BRAND } from '../config';
import { useModuleCode } from '../hooks/module';
import { menuFor, menuHref } from '../lib/roles';
import { NotFoundPage } from '../pages/NotFoundPage';

/** True when the menu href matches the current address (path + mine filter). */
function isActive(href: string, pathname: string, search: string): boolean {
  const [path, query = ''] = href.split('?');
  if (path !== pathname.replace(/\/$/, '')) return false;
  const want = new URLSearchParams(query).get('mine') ?? '';
  const have = new URLSearchParams(search).get('mine') ?? '';
  return want === have;
}

export function AppLayout() {
  const me = useMe();
  const module = useModuleCode();
  const location = useLocation();
  const navigate = useNavigate();
  const reset = useResetSession();
  const [opened, { toggle, close }] = useDisclosure();

  const current = me.modules.find((m) => m.slug === module);
  if (!current) return <NotFoundPage message={`Unknown payment module "${module}".`} />;

  const items = menuFor(me);

  const switchModule = (code: string | null) => {
    if (!code) return;
    // Same screen in the other module; the case id would not carry over.
    const rest = location.pathname.split('/').slice(2);
    const section = rest[0] === 'cases' ? '/cases' : '';
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
              active={isActive(href, location.pathname, location.search)}
            />
          );
        })}
      </AppShell.Navbar>

      <AppShell.Main id="main" tabIndex={-1} style={{ outline: 'none' }}>
        <Outlet />
      </AppShell.Main>
      <SessionWatcher />
    </AppShell>
  );
}
