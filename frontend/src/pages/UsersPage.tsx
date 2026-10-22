/**
 * Users and roles, read-only (design 4.17, Slice 4 contract C1): who holds
 * which role, for the Administrator. Editing stays in the Django admin
 * (P3); the screen links to it when the server gives the address (staff
 * status). The Auditor is not given this list by the design (open question
 * to the owner, see the contract).
 */
import { Alert, Badge, Button, Group, Select, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';

import { ApiError, listUsersAndRoles } from '../api/client';
import { useMe } from '../auth/auth';
import { ListPager } from '../components/ListPager';
import { formatCount, formatDateTime } from '../lib/format';
import { can, ROLES } from '../lib/roles';
import { pageBounds, useUrlFilters } from '../lib/urlFilters';

const PAGE_SIZE = 50;
const FILTER_KEYS = ['role', 'active', 'search'] as const;

function SearchBox({ value, onApply }: { value: string; onApply: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onApply(draft.trim());
  };
  return (
    <form onSubmit={submit} aria-label="Search users">
      <Group gap="xs" align="flex-end">
        <TextInput
          label="Name or user name"
          value={draft}
          onChange={(e) => setDraft(e.currentTarget.value)}
          w={220}
        />
        <Button type="submit" variant="default">
          Search
        </Button>
      </Group>
    </form>
  );
}

export function UsersPage() {
  const me = useMe();
  const allowed = can(me.permissions, 'users.admin');
  const { filters, setFilter, reset } = useUrlFilters(FILTER_KEYS, PAGE_SIZE);
  const query = useQuery({
    queryKey: ['users-and-roles', filters],
    queryFn: () => listUsersAndRoles(filters),
    placeholderData: keepPreviousData,
    enabled: allowed,
  });
  const data = query.data;
  const { pages } = pageBounds(data?.count ?? 0, filters.page, PAGE_SIZE);
  const adminUrl = data?.admin_users_url ?? null;

  if (!allowed) {
    return (
      <Alert color="yellow" title="Users and roles" role="alert">
        Only an Administrator sees the users and roles.
      </Alert>
    );
  }

  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={1} size="h3">
          Users and roles
        </Title>
        {adminUrl && (
          <Button component="a" href={adminUrl} variant="default">
            Edit users in the Django admin
          </Button>
        )}
      </Group>
      <Text size="sm" c="dimmed">
        Read-only. Accounts and roles are changed in the Django admin.
      </Text>

      <Group gap="xs" align="flex-end" wrap="wrap" component="section" aria-label="Filters">
        <Select
          label="Role"
          data={[{ value: '', label: 'Any' }, ...Object.values(ROLES).map((r) => ({ value: r, label: r }))]}
          value={filters.role ?? ''}
          onChange={(v) => setFilter('role', v)}
          allowDeselect={false}
          w={190}
        />
        <Select
          label="Account status"
          data={[
            { value: '', label: 'Any' },
            { value: 'true', label: 'Active' },
            { value: 'false', label: 'Inactive' },
          ]}
          value={filters.active ?? ''}
          onChange={(v) => setFilter('active', v)}
          allowDeselect={false}
          w={150}
        />
        <SearchBox
          key={filters.search ?? ''}
          value={filters.search ?? ''}
          onApply={(v) => setFilter('search', v)}
        />
        <Button variant="subtle" onClick={reset}>
          Reset filters
        </Button>
      </Group>

      {query.error && (
        <Alert color="red" role="alert" title="The users could not be loaded">
          {query.error instanceof ApiError && query.error.isForbidden
            ? 'Your role may not see the users and roles.'
            : query.error.message}
        </Alert>
      )}
      <Text size="sm" aria-live="polite">
        {query.isPending ? 'Loading...' : `${formatCount(data?.count ?? 0)} account(s)`}
      </Text>
      <Table.ScrollContainer minWidth={800}>
        <Table withTableBorder aria-label="Users and roles" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {['User name', 'Name', 'Roles', 'Status', 'Last login'].map((h) => (
                <Table.Th key={h} scope="col">
                  {h}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {(data?.results ?? []).map((u) => (
              <Table.Tr key={u.id}>
                <Table.Td>
                  {u.username}{' '}
                  {u.is_superuser && (
                    <Badge size="sm" color="grape" variant="light">
                      Superuser
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>{u.full_name || '-'}</Table.Td>
                <Table.Td>
                  {u.roles.length ? (
                    u.roles.join(', ')
                  ) : (
                    <Text c="dimmed" span>
                      No role
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  {u.is_active ? (
                    'Active'
                  ) : (
                    <Badge size="sm" color="gray" variant="light">
                      Inactive
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>{u.last_login ? formatDateTime(u.last_login) : 'Never'}</Table.Td>
              </Table.Tr>
            ))}
            {!query.isPending && (data?.results.length ?? 0) === 0 && (
              <Table.Tr>
                <Table.Td colSpan={5}>No account matches these filters.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <ListPager
        total={pages}
        value={filters.page}
        onChange={(p) => setFilter('page', String(p))}
        label="Users"
      />
      {!adminUrl && data && (
        <Text size="xs" c="dimmed">
          The link to edit users in the Django admin needs Django staff status on your account.
        </Text>
      )}
    </Stack>
  );
}
