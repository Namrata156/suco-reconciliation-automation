/**
 * User activity report (Phase 6 design 2.7, contract B7): logins and work
 * done per user in a period. Administrator and Auditor only (it holds IP
 * addresses and failed logins). Not under a module: logins are global; the
 * module filter narrows the WORK counts only. Every user is listed, also
 * those with no activity. "Last login" is the last one inside the range.
 * Each row links to the audit lists for that user and range.
 */
import { Anchor, Badge, Group, Select, Stack, Table, Text, TextInput } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { getUserActivityReport, reportPaths } from '../api/client';
import { useMe } from '../auth/auth';
import { ListPager } from '../components/ListPager';
import { ExportButton } from '../features/reports/ExportButton';
import { ReportFilterBar, useReportRange } from '../features/reports/ReportFilterBar';
import { ReportFrame } from '../features/reports/ReportFrame';
import { ANY_YES_NO, NoReportAccess, Num, ReportProblems } from '../features/reports/reportParts';
import { formatCount, formatDate, formatDateTime } from '../lib/format';
import { spaLink } from '../lib/links';
import { allows, ROLES } from '../lib/roles';
import { pageBounds } from '../lib/urlFilters';

export const USER_ACTIVITY_PAGE_SIZE = 50;

function UserFilter({ value, onApply }: { value: string | undefined; onApply: (v: string) => void }) {
  const [text, setText] = useState(value ?? '');
  const apply = () => {
    if (text.trim() !== (value ?? '')) onApply(text.trim());
  };
  return (
    <TextInput
      label="User"
      placeholder="username"
      value={text}
      onChange={(e) => setText(e.currentTarget.value)}
      onBlur={apply}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          apply();
        }
      }}
      w={150}
    />
  );
}

export function UserActivityReportPage() {
  const me = useMe();
  const allowed = allows(me.permissions, 'reports.user_activity');
  const { range, error: rangeErr } = useReportRange({ basis: 'event' });
  const [params, setParams] = useSearchParams();
  const pageNo = Math.max(1, Math.floor(Number(params.get('page') || '1')) || 1);
  const get = (k: string) => params.get(k) || undefined;
  const filters = {
    date_from: range?.date_from,
    date_to: range?.date_to,
    user: get('user'),
    role: get('role'),
    module: get('module'),
    active: get('active'),
    page: pageNo,
    page_size: USER_ACTIVITY_PAGE_SIZE,
  };
  const query = useQuery({
    queryKey: ['report', 'user-activity', filters],
    queryFn: () => getUserActivityReport(filters),
    placeholderData: keepPreviousData,
    enabled: allowed && !!range,
  });
  const set = (key: string, value: string | null | undefined) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== 'page') next.delete('page');
      return next;
    });

  if (!allowed) return <NoReportAccess title="User activity report" />;
  const data = query.data;
  const rows = data?.results ?? [];
  const { pages } = pageBounds(data?.count ?? 0, pageNo, USER_ACTIVITY_PAGE_SIZE);
  const moduleName = filters.module
    ? (me.modules.find((m) => m.slug === filters.module)?.code ?? filters.module.toUpperCase())
    : null;

  return (
    <ReportFrame
      title="User activity report"
      description="Logins and work done per user in a period. Dates are the days the events happened."
      range={range}
      scopeLine={range ? `Dates: ${formatDate(range.date_from)} to ${formatDate(range.date_to)}` : undefined}
      moduleLabel={
        moduleName ? `${moduleName} (work counts only; logins are for every module)` : 'All modules'
      }
      report={data?.report}
      filterLines={[
        ...(filters.user ? [`User: ${filters.user}`] : []),
        ...(filters.role ? [`Role: ${filters.role}`] : []),
        ...(filters.active ? [`Active users: ${filters.active === 'true' ? 'yes' : 'no'}`] : []),
      ]}
      updatedAt={query.dataUpdatedAt}
      pageLine={pages > 1 ? `Page ${pageNo} of ${pages}` : undefined}
      onRefresh={() => void query.refetch()}
      refreshing={query.isFetching}
      actions={
        <ExportButton
          info={data?.export}
          path={reportPaths.userActivity()}
          query={filters}
          fallbackName={`user_activity_${range?.date_from}_to_${range?.date_to}`}
        />
      }
      filters={
        <ReportFilterBar basis="event" dateLabel="Date" showModule={false}>
          <UserFilter key={filters.user ?? ''} value={filters.user} onApply={(v) => set('user', v)} />
          <Select
            label="Role"
            data={[{ value: '', label: 'Any' }, ...Object.values(ROLES).map((r) => ({ value: r, label: r }))]}
            value={filters.role ?? ''}
            onChange={(v) => set('role', v)}
            allowDeselect={false}
            w={170}
          />
          <Select
            label="Module (work counts)"
            data={[
              { value: '', label: 'All modules' },
              ...me.modules.map((m) => ({ value: m.slug, label: m.code })),
            ]}
            value={filters.module ?? ''}
            onChange={(v) => set('module', v)}
            allowDeselect={false}
            w={180}
          />
          <Select
            label="Account"
            data={ANY_YES_NO('Active', 'Deactivated')}
            value={filters.active ?? ''}
            onChange={(v) => set('active', v)}
            allowDeselect={false}
            w={140}
          />
        </ReportFilterBar>
      }
    >
      <ReportProblems rangeError={rangeErr} error={query.error} />
      {range && (
        <Table.ScrollContainer minWidth={1300}>
          <Table striped aria-label="Activity per user" aria-busy={query.isFetching}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">User</Table.Th>
                <Table.Th scope="col" ta="right">
                  Logins
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Failed logins
                </Table.Th>
                <Table.Th scope="col">Last login in the range</Table.Th>
                <Table.Th scope="col">Cases</Table.Th>
                <Table.Th scope="col">Files and runs</Table.Th>
                <Table.Th scope="col">Settings</Table.Th>
                <Table.Th scope="col" ta="right">
                  Report downloads
                </Table.Th>
                <Table.Th scope="col" className="no-print">
                  Audit trail
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((u) => {
                const c = u.cases;
                const links = [
                  ['Case events', u.links.case_events],
                  ['Logins', u.links.user_activity],
                  ['Operations', u.links.operations],
                ] as const;
                return (
                  <Table.Tr key={u.user_id}>
                    <Table.Td>
                      <Text size="sm" fw={600}>
                        {u.username}
                      </Text>
                      {u.full_name !== u.username && <Text size="xs">{u.full_name}</Text>}
                      <Text size="xs" c="dimmed">
                        {u.roles.length ? u.roles.join(', ') : 'No role'}
                      </Text>
                      {!u.is_active && (
                        <Badge color="gray" variant="outline" size="sm">
                          Deactivated
                        </Badge>
                      )}
                    </Table.Td>
                    <Num>{formatCount(u.logins)}</Num>
                    <Num c={u.failed_logins ? 'red' : undefined}>{formatCount(u.failed_logins)}</Num>
                    <Table.Td>
                      {u.last_login ? (
                        <>
                          <Text size="xs">{formatDateTime(u.last_login)}</Text>
                          <Text size="xs" c="dimmed">
                            IP {u.last_login_ip ?? 'not recorded'}
                          </Text>
                        </>
                      ) : (
                        '-'
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs">
                        Taken {formatCount(c.taken)}, submitted {formatCount(c.submitted)}, approved{' '}
                        {formatCount(c.approved)}, rejected {formatCount(c.rejected)}, marked done{' '}
                        {formatCount(c.marked_done)}, remarks {formatCount(c.remarks)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Stack gap={0}>
                        <Text size="xs">
                          Uploaded {formatCount(u.files.uploaded)}, refused {formatCount(u.files.refused)}
                        </Text>
                        <Text size="xs">
                          CBS dates chosen {formatCount(u.cbs_dates_selected)}, runs{' '}
                          {formatCount(u.runs_started)}
                        </Text>
                      </Stack>
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs">
                        Proposed {formatCount(u.settings.proposed)}, decided {formatCount(u.settings.decided)}
                      </Text>
                    </Table.Td>
                    <Num>{formatCount(u.report_downloads)}</Num>
                    <Table.Td className="no-print">
                      <Group gap={6}>
                        {links.map(([label, link]) => {
                          const to = spaLink(link);
                          return to ? (
                            <Anchor
                              key={label}
                              component={Link}
                              to={to}
                              size="xs"
                              aria-label={`${label} of ${u.username}`}
                            >
                              {label}
                            </Anchor>
                          ) : null;
                        })}
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
              {!query.isPending && !query.error && rows.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={9}>No user matches these filters.</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      <div className="no-print">
        <ListPager total={pages} value={pageNo} onChange={(n) => set('page', String(n))} label="Users" />
      </div>
    </ReportFrame>
  );
}
