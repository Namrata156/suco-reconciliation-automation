/**
 * Phase 6 slice 6.3 case reports (design 2.4-2.6; contract B4, B5, B6, B6s).
 * No priority anywhere: no column, filter or breakdown (owner 2026-11-08).
 *
 *  - Exception report (B4): cases of a business-date range, open or closed,
 *    with the amount, the action proposed and its decision, the last remark.
 *    Settlement cases show their current unexplained amount and have no
 *    ageing bucket.
 *  - Ageing report (B5): ACTIVE cases as of now, rule x ageing bucket; each
 *    cell opens the case queue filtered to it (ageing_bucket and rule_code).
 *    Settlement cases have no bucket: they are counted apart ("not_aged").
 *  - Approval report (B6 / B6s, Administrator, Auditor, Checker): case action
 *    decisions and settings changes, on the DECISION date (the submission
 *    date for ones still pending).
 *
 * Owner 2026-11-09: a case the system closed (no-money reply in an SSKUPI
 * file) carries a "Closed by system" badge in B4 (with a filter) and on its
 * withdrawn / approved action in B6, where an empty checker reads "System".
 */
import {
  Alert,
  Anchor,
  Badge,
  Card,
  Group,
  MultiSelect,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import {
  getAgeingReport,
  getApprovalReport,
  getApprovalSettingsReport,
  getExceptionReport,
  reportPaths,
} from '../api/client';
import type { AgeingCell, ExceptionReportRow, ModuleMeta } from '../api/types';
import { useMe } from '../auth/auth';
import { ClosedBySystemBadge, DecisionBadge, SelfApprovedBadge, StatusBadge } from '../components/Badges';
import { ListPager } from '../components/ListPager';
import { ExportButton } from '../features/reports/ExportButton';
import { ReportFilterBar, useReportRange } from '../features/reports/ReportFilterBar';
import { ReportFrame } from '../features/reports/ReportFrame';
import {
  ANY_YES_NO,
  joinCsv,
  NoReportAccess,
  Num,
  ReportProblems,
  splitCsv,
} from '../features/reports/reportParts';
import { labelOf, useMeta, useModuleCode } from '../hooks/module';
import { formatAmount, formatCount, formatDate, formatDateTime } from '../lib/format';
import { spaLink } from '../lib/links';
import { allows } from '../lib/roles';
import { pageBounds } from '../lib/urlFilters';
import { orderingChoices } from './CaseQueuePage';

export const CASE_REPORT_PAGE_SIZE = 50;

/** Report filters kept in the address; a change goes back to page 1. */
function useAddressFilters() {
  const [params, setParams] = useSearchParams();
  const pageNo = Math.max(1, Math.floor(Number(params.get('page') || '1')) || 1);
  const get = (k: string) => params.get(k) || undefined;
  const set = (key: string, value: string | null | undefined) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== 'page') next.delete('page');
      return next;
    });
  return { params, pageNo, get, set };
}

/** A text filter applied with Enter or on leaving the field, not on every key. */
function TextFilter({
  label,
  value,
  onApply,
  w = 170,
  placeholder,
}: {
  label: string;
  value: string | undefined;
  onApply: (v: string) => void;
  w?: number;
  placeholder?: string;
}) {
  const [text, setText] = useState(value ?? '');
  const apply = () => {
    if (text.trim() !== (value ?? '')) onApply(text.trim());
  };
  return (
    <TextInput
      label={label}
      value={text}
      placeholder={placeholder}
      onChange={(e) => setText(e.currentTarget.value)}
      onBlur={apply}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          apply();
        }
      }}
      w={w}
    />
  );
}

const ruleOptions = (meta: ModuleMeta | undefined) =>
  (meta?.rules ?? []).map((r) => ({ value: r.code, label: `${r.code} ${r.name}`.trim() }));

/** A case as the queue names it: the RRN, or "Settlement (NTSL) <date>". */
const caseName = (c: Pick<ExceptionReportRow, 'case_type' | 'rrn' | 'business_date'>) =>
  c.case_type === 'SETTLEMENT' ? `Settlement (NTSL) ${formatDate(c.business_date)}` : c.rrn;

// ===========================================================================
// B4 exception report
// ===========================================================================
export function ExceptionReportPage() {
  const allowed = allows(useMe().permissions, 'reports.view');
  const module = useModuleCode();
  const { data: meta } = useMeta(module);
  const { range, error: rangeErr } = useReportRange();
  const f = useAddressFilters();
  const rules = splitCsv(f.get('rule_code'));
  const filters = {
    date_from: range?.date_from,
    date_to: range?.date_to,
    status: f.get('status'),
    rule_code: rules.length ? joinCsv(rules) : undefined,
    case_type: f.get('case_type'),
    overdue: f.get('overdue'),
    assigned_to: f.get('assigned_to'),
    closed_by_system: f.get('closed_by_system'),
    ordering: f.get('ordering'),
    page: f.pageNo,
    page_size: CASE_REPORT_PAGE_SIZE,
  };
  const query = useQuery({
    queryKey: ['report', 'exceptions', module, filters],
    queryFn: () => getExceptionReport(module, filters),
    placeholderData: keepPreviousData,
    enabled: allowed && !!range,
  });
  if (!allowed) return <NoReportAccess title="Exception report" what="the reports" />;

  const data = query.data;
  const rows = data?.results ?? [];
  const { pages } = pageBounds(data?.count ?? 0, f.pageNo, CASE_REPORT_PAGE_SIZE);
  const statusData = [
    { value: '', label: 'Active (default)' },
    { value: 'all', label: 'All statuses' },
    ...(meta?.case_statuses ?? []),
  ];
  const statusText = statusData.find((s) => s.value === (filters.status ?? ''))?.label ?? filters.status;
  const actionLabel = (v: string) => meta?.action_types.find((a) => a.value === v)?.label ?? v;

  return (
    <ReportFrame
      title="Exception report"
      description="Exception cases of the business dates, open or closed, with the action taken."
      range={range}
      report={data?.report}
      filterLines={[
        `Status: ${statusText}`,
        ...(rules.length ? [`Rules: ${rules.join(', ')}`] : []),
        ...(filters.case_type ? [`Case type: ${labelOf(meta?.case_types, filters.case_type)}`] : []),
        ...(filters.overdue ? [`Overdue: ${filters.overdue === 'true' ? 'yes' : 'no'}`] : []),
        ...(filters.assigned_to ? [`Assigned to: ${filters.assigned_to}`] : []),
        ...(filters.closed_by_system
          ? [`Closed by system: ${filters.closed_by_system === 'true' ? 'yes' : 'no'}`]
          : []),
      ]}
      updatedAt={query.dataUpdatedAt}
      pageLine={pages > 1 ? `Page ${f.pageNo} of ${pages}` : undefined}
      onRefresh={() => void query.refetch()}
      refreshing={query.isFetching}
      actions={
        <ExportButton
          info={data?.export}
          path={reportPaths.exceptions(module)}
          query={filters}
          fallbackName={`exception_report_${range?.date_from}_to_${range?.date_to}`}
        />
      }
      filters={
        <ReportFilterBar>
          <Select
            label="Status"
            data={statusData}
            value={filters.status ?? ''}
            onChange={(v) => f.set('status', v)}
            allowDeselect={false}
            w={170}
          />
          <MultiSelect
            label="Rules"
            placeholder={rules.length ? undefined : 'All'}
            data={ruleOptions(meta)}
            value={rules}
            onChange={(v) => f.set('rule_code', joinCsv(v))}
            searchable
            clearable
            w={220}
          />
          <Select
            label="Case type"
            data={[{ value: '', label: 'Any' }, ...(meta?.case_types ?? [])]}
            value={filters.case_type ?? ''}
            onChange={(v) => f.set('case_type', v)}
            allowDeselect={false}
            w={140}
          />
          <Select
            label="Overdue"
            data={ANY_YES_NO('Overdue', 'Not overdue')}
            value={filters.overdue ?? ''}
            onChange={(v) => f.set('overdue', v)}
            allowDeselect={false}
            w={130}
          />
          <TextFilter
            key={filters.assigned_to ?? ''}
            label="Assigned to"
            placeholder='username, or "none"'
            value={filters.assigned_to}
            onApply={(v) => f.set('assigned_to', v)}
          />
          <Select
            label="Closed by system"
            data={ANY_YES_NO('Closed by system', 'Not closed by system')}
            value={filters.closed_by_system ?? ''}
            onChange={(v) => f.set('closed_by_system', v)}
            allowDeselect={false}
            w={190}
          />
          <Select
            label="Sort"
            data={[{ value: '', label: 'Queue order (default)' }, ...orderingChoices(meta?.case_orderings)]}
            value={filters.ordering ?? ''}
            onChange={(v) => f.set('ordering', v)}
            allowDeselect={false}
            w={200}
          />
        </ReportFilterBar>
      }
    >
      <ReportProblems rangeError={rangeErr} error={query.error} />
      {data && (
        <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
          <Card withBorder padding="sm" role="group" aria-label="Cases by status">
            <Text size="sm" fw={600}>
              {formatCount(data.count)} case(s) by status
            </Text>
            {data.summary.by_status.length === 0 && <Text size="sm">None.</Text>}
            {data.summary.by_status.map((s) => (
              <Text size="sm" key={s.status}>
                {labelOf(meta?.case_statuses, s.status)}: {formatCount(s.count)}
              </Text>
            ))}
          </Card>
          <Card withBorder padding="sm">
            <Table aria-label="Cases by rule" withRowBorders={false}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Rule</Table.Th>
                  <Table.Th scope="col" ta="right">
                    Cases
                  </Table.Th>
                  <Table.Th scope="col" ta="right">
                    Still open
                  </Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.summary.by_rule.map((r) => (
                  <Table.Tr key={r.rule_code || 'settlement'}>
                    <Table.Td>{r.rule_code || 'Settlement (no rule)'}</Table.Td>
                    <Num>{formatCount(r.count)}</Num>
                    <Num>{formatCount(r.open)}</Num>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Card>
        </SimpleGrid>
      )}
      {range && (
        <Table.ScrollContainer minWidth={1400}>
          <Table striped aria-label="Exception cases" aria-busy={query.isFetching}>
            <Table.Thead>
              <Table.Tr>
                {[
                  'Case (RRN)',
                  'Business date',
                  'Rule and outcome',
                  'Amount',
                  'Status',
                  'Age',
                  'Due',
                  'Assigned to',
                  'Proposed action',
                  'Created / closed',
                  'Last remark',
                ].map((h) => (
                  <Table.Th scope="col" key={h} ta={h === 'Amount' || h === 'Age' ? 'right' : undefined}>
                    {h}
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((c) => {
                const to = spaLink(c.link);
                const a = c.proposed_action;
                return (
                  <Table.Tr key={c.id}>
                    <Table.Td className="wrap-anywhere">
                      {to ? (
                        <Anchor component={Link} to={to}>
                          {caseName(c)}
                        </Anchor>
                      ) : (
                        caseName(c)
                      )}
                    </Table.Td>
                    <Table.Td>{formatDate(c.business_date)}</Table.Td>
                    <Table.Td>
                      {c.rule_code || '-'}
                      <Text size="xs" c="dimmed">
                        {labelOf(meta?.status_categories, c.status_category)}
                      </Text>
                    </Table.Td>
                    <Num>{formatAmount(c.amount)}</Num>
                    <Table.Td>
                      <Group gap={4}>
                        <StatusBadge value={c.status} choices={meta?.case_statuses} />
                        {c.closed_by_system && <ClosedBySystemBadge />}
                      </Group>
                    </Table.Td>
                    <Num>
                      {formatCount(c.age_days)} d
                      {c.ageing_bucket && (
                        <Text size="xs" c="dimmed">
                          {c.ageing_bucket}
                        </Text>
                      )}
                    </Num>
                    <Table.Td>
                      {formatDate(c.due_date)}
                      {c.is_overdue && (
                        <Badge color="red" variant="light" ml={4}>
                          Overdue
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>{c.assigned_to ?? 'Unassigned'}</Table.Td>
                    <Table.Td>
                      {a ? (
                        <Stack gap={2}>
                          <Text size="xs">{actionLabel(a.action_type)}</Text>
                          {a.response_code && (
                            <Text size="xs" c="dimmed">
                              {a.response_code}
                            </Text>
                          )}
                          <DecisionBadge value={a.decision} />
                          {a.reference && (
                            <Text size="xs" c="dimmed" className="wrap-anywhere">
                              Ref: {a.reference}
                            </Text>
                          )}
                        </Stack>
                      ) : (
                        '-'
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs">{formatDateTime(c.created_at)}</Text>
                      {c.closed_at && (
                        <Text size="xs" c="dimmed">
                          Closed {formatDateTime(c.closed_at)}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td className="wrap-anywhere">
                      <Text size="xs">{c.last_remark || '-'}</Text>
                    </Table.Td>
                  </Table.Tr>
                );
              })}
              {!query.isPending && !query.error && rows.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={11}>No cases match these dates and filters.</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      <div className="no-print">
        <ListPager total={pages} value={f.pageNo} onChange={(n) => f.set('page', String(n))} label="Cases" />
      </div>
    </ReportFrame>
  );
}

// ===========================================================================
// B5 ageing report
// ===========================================================================
function AgeingCellView({ cell, name }: { cell: AgeingCell; name: string }) {
  if (cell.count === 0) return <Num c="dimmed">-</Num>;
  const to = spaLink(cell.link);
  return (
    <Num>
      {to ? (
        <Anchor
          component={Link}
          to={to}
          aria-label={`${formatCount(cell.count)} cases, ${name}, ${cell.bucket}`}
        >
          {formatCount(cell.count)}
        </Anchor>
      ) : (
        formatCount(cell.count)
      )}
      <Text size="xs" c="dimmed">
        {formatAmount(cell.amount)}
      </Text>
      {cell.overdue > 0 && (
        <Text size="xs" c="red">
          {formatCount(cell.overdue)} overdue
        </Text>
      )}
    </Num>
  );
}

export function AgeingReportPage() {
  const allowed = allows(useMe().permissions, 'reports.view');
  const module = useModuleCode();
  const { data: meta } = useMeta(module);
  const f = useAddressFilters();
  const rules = splitCsv(f.get('rule_code'));
  const filters = { rule_code: rules.length ? joinCsv(rules) : undefined, case_type: f.get('case_type') };
  const query = useQuery({
    queryKey: ['report', 'ageing', module, filters],
    queryFn: () => getAgeingReport(module, filters),
    placeholderData: keepPreviousData,
    enabled: allowed,
  });
  if (!allowed) return <NoReportAccess title="Ageing report" what="the reports" />;
  const data = query.data;
  const buckets = data?.buckets ?? [];
  const oldest = data?.oldest_case;
  const oldestLink = spaLink(oldest?.link);
  const notAgedLink = spaLink(data?.not_aged.link);

  return (
    <ReportFrame
      title="Ageing report"
      description="Open cases by age bucket and rule, as of now. Click a count to open those cases."
      range={null}
      scopeLine={`Open cases as of ${data ? formatDateTime(data.report.generated_at) : '-'}`}
      report={data?.report}
      filterLines={[
        ...(rules.length ? [`Rules: ${rules.join(', ')}`] : []),
        ...(filters.case_type ? [`Case type: ${labelOf(meta?.case_types, filters.case_type)}`] : []),
      ]}
      updatedAt={query.dataUpdatedAt}
      onRefresh={() => void query.refetch()}
      refreshing={query.isFetching}
      actions={
        <ExportButton
          info={data?.export}
          path={reportPaths.ageing(module)}
          query={filters}
          fallbackName="ageing"
        />
      }
      filters={
        <section aria-label="Report filters" className="no-print">
          <Group gap="xs" align="flex-end" wrap="wrap">
            <MultiSelect
              label="Rules"
              placeholder={rules.length ? undefined : 'All'}
              data={ruleOptions(meta)}
              value={rules}
              onChange={(v) => f.set('rule_code', joinCsv(v))}
              searchable
              clearable
              w={260}
            />
            <Select
              label="Case type"
              data={[{ value: '', label: 'Any' }, ...(meta?.case_types ?? [])]}
              value={filters.case_type ?? ''}
              onChange={(v) => f.set('case_type', v)}
              allowDeselect={false}
              w={150}
            />
          </Group>
        </section>
      }
    >
      <ReportProblems error={query.error} />
      {data && (
        <Table.ScrollContainer minWidth={900}>
          <Table withTableBorder aria-label="Open cases by rule and age" aria-busy={query.isFetching}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Rule</Table.Th>
                {buckets.map((b) => (
                  <Table.Th scope="col" ta="right" key={b.label}>
                    {b.label}
                  </Table.Th>
                ))}
                <Table.Th scope="col" ta="right">
                  Total
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.by_rule.map((r) => (
                <Table.Tr key={r.rule_code}>
                  <Table.Th scope="row" title={meta?.rules.find((x) => x.code === r.rule_code)?.name}>
                    {r.rule_code}
                  </Table.Th>
                  {r.cells.map((c) => (
                    <AgeingCellView key={c.bucket} cell={c} name={r.rule_code} />
                  ))}
                  <Num fw={600}>
                    {formatCount(r.total.count)}
                    <Text size="xs" c="dimmed">
                      {formatAmount(r.total.amount)}
                    </Text>
                  </Num>
                </Table.Tr>
              ))}
              {data.by_rule.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={buckets.length + 2}>No open case with an ageing bucket.</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
            {data.by_rule.length > 0 && (
              <Table.Tfoot>
                <Table.Tr fw={600}>
                  <Table.Th scope="row">Total</Table.Th>
                  {data.totals.cells.map((c) => (
                    <AgeingCellView key={c.bucket} cell={c} name="all rules" />
                  ))}
                  <Num>
                    {formatCount(data.totals.total.count)}
                    <Text size="xs" c="dimmed">
                      {formatAmount(data.totals.total.amount)}
                    </Text>
                    {data.totals.total.overdue > 0 && (
                      <Text size="xs" c="red">
                        {formatCount(data.totals.total.overdue)} overdue
                      </Text>
                    )}
                  </Num>
                </Table.Tr>
              </Table.Tfoot>
            )}
          </Table>
        </Table.ScrollContainer>
      )}
      {data && data.not_aged.count > 0 && (
        <Card withBorder padding="sm" role="group" aria-label="Open cases with no ageing bucket">
          <Title order={2} size="h5">
            Not aged: settlement cases
          </Title>
          <Text size="sm">
            {notAgedLink ? (
              <Anchor component={Link} to={notAgedLink}>
                {formatCount(data.not_aged.count)} open case(s)
              </Anchor>
            ) : (
              `${formatCount(data.not_aged.count)} open case(s)`
            )}
            , unexplained amount {formatAmount(data.not_aged.amount)}. Settlement cases have no due date, so
            they are not in the age buckets above.
          </Text>
        </Card>
      )}
      {oldest && (
        <Text size="sm">
          Oldest open case:{' '}
          {oldestLink ? (
            <Anchor component={Link} to={oldestLink}>
              {oldest.rrn}
            </Anchor>
          ) : (
            oldest.rrn
          )}
          , {formatCount(oldest.age_days)} days old.
        </Text>
      )}
    </ReportFrame>
  );
}

// ===========================================================================
// B6 / B6s approval report
// ===========================================================================
const SETTINGS_DECISIONS = [
  { value: 'APPROVED', label: 'Approved' },
  { value: 'REJECTED', label: 'Rejected' },
  { value: 'PENDING', label: 'Pending' },
];
const SETTINGS_KINDS = [
  { value: 'response_code', label: 'Response codes' },
  { value: 'ntsl_mapping', label: 'NTSL mapping' },
  { value: 'tolerance', label: 'Tolerance' },
];
const ACTION_DECISIONS = [...SETTINGS_DECISIONS, { value: 'WITHDRAWN', label: 'Withdrawn' }];
/** A system closure withdraws a pending action or marks an approved one done (owner 2026-11-09). */
const SYSTEM_SETTLED = ['WITHDRAWN', 'APPROVED'];

export function ApprovalReportPage() {
  const allowed = allows(useMe().permissions, 'reports.approvals');
  const module = useModuleCode();
  const { data: meta } = useMeta(module);
  const { range, error: rangeErr } = useReportRange({ basis: 'event' });
  const f = useAddressFilters();
  const tab = f.get('tab') === 'settings' ? 'settings' : 'actions';
  const decisionOptions = tab === 'actions' ? ACTION_DECISIONS : SETTINGS_DECISIONS;
  // Withdrawn exists for case actions only; the settings list would refuse it.
  const decisions = splitCsv(f.get('decision')).filter((d) => decisionOptions.some((o) => o.value === d));
  const actionTypes = splitCsv(f.get('action_type'));
  const kinds = splitCsv(f.get('kind'));
  const filters = {
    date_from: range?.date_from,
    date_to: range?.date_to,
    decision: decisions.length ? joinCsv(decisions) : undefined,
    maker: f.get('maker'),
    checker: f.get('checker'),
    self_approved: f.get('self_approved'),
    ...(tab === 'actions'
      ? { action_type: actionTypes.length ? joinCsv(actionTypes) : undefined }
      : { kind: kinds.length ? joinCsv(kinds) : undefined }),
    page: f.pageNo,
    page_size: CASE_REPORT_PAGE_SIZE,
  };
  const actions = useQuery({
    queryKey: ['report', 'approvals', module, filters],
    queryFn: () => getApprovalReport(module, filters),
    placeholderData: keepPreviousData,
    enabled: allowed && !!range && tab === 'actions',
  });
  const settings = useQuery({
    queryKey: ['report', 'approvals-settings', module, filters],
    queryFn: () => getApprovalSettingsReport(module, filters),
    placeholderData: keepPreviousData,
    enabled: allowed && !!range && tab === 'settings',
  });
  if (!allowed) return <NoReportAccess title="Approval report" />;

  const query = tab === 'actions' ? actions : settings;
  const data = query.data;
  const { pages } = pageBounds(data?.count ?? 0, f.pageNo, CASE_REPORT_PAGE_SIZE);
  const setTab = (v: string | null) => f.set('tab', v === 'settings' ? 'settings' : '');

  const actionRows = actions.data?.results ?? [];
  const settingRows = settings.data?.results ?? [];
  const summary = actions.data?.summary;

  return (
    <ReportFrame
      title="Approval report"
      description="Maker-checker decisions in a period: who proposed, who decided, self-approvals. Dates are decision dates (submission dates for pending ones)."
      range={range}
      scopeLine={
        range ? `Decision dates: ${formatDate(range.date_from)} to ${formatDate(range.date_to)}` : undefined
      }
      report={data?.report}
      filterLines={[
        tab === 'actions' ? 'Case actions' : 'Settings changes',
        ...(decisions.length ? [`Decision: ${decisions.join(', ')}`] : []),
        ...(filters.maker ? [`Maker: ${filters.maker}`] : []),
        ...(filters.checker ? [`Checker: ${filters.checker}`] : []),
        ...(filters.self_approved
          ? [`Self-approved: ${filters.self_approved === 'true' ? 'yes' : 'no'}`]
          : []),
      ]}
      updatedAt={query.dataUpdatedAt}
      pageLine={pages > 1 ? `Page ${f.pageNo} of ${pages}` : undefined}
      onRefresh={() => void query.refetch()}
      refreshing={query.isFetching}
      actions={
        <ExportButton
          info={data?.export}
          path={tab === 'actions' ? reportPaths.approvals(module) : reportPaths.approvalSettings(module)}
          query={filters}
          defaultSheet={tab}
          fallbackName={`approvals_${range?.date_from}_to_${range?.date_to}`}
        />
      }
      filters={
        <ReportFilterBar basis="event" dateLabel="Decision date">
          <MultiSelect
            label="Decision"
            placeholder={decisions.length ? undefined : 'Any'}
            data={decisionOptions}
            value={decisions}
            onChange={(v) => f.set('decision', joinCsv(v))}
            clearable
            w={220}
          />
          <TextFilter
            key={`m${filters.maker ?? ''}`}
            label="Maker"
            placeholder="username"
            value={filters.maker}
            onApply={(v) => f.set('maker', v)}
            w={140}
          />
          <TextFilter
            key={`c${filters.checker ?? ''}`}
            label="Checker"
            placeholder="username"
            value={filters.checker}
            onApply={(v) => f.set('checker', v)}
            w={140}
          />
          <Select
            label="Self-approved"
            data={ANY_YES_NO('Self-approved', 'Not self-approved')}
            value={filters.self_approved ?? ''}
            onChange={(v) => f.set('self_approved', v)}
            allowDeselect={false}
            w={170}
          />
          {tab === 'actions' ? (
            <MultiSelect
              label="Action"
              placeholder={actionTypes.length ? undefined : 'Any'}
              data={(meta?.action_types ?? []).map((a) => ({ value: a.value, label: a.label }))}
              value={actionTypes}
              onChange={(v) => f.set('action_type', joinCsv(v))}
              searchable
              clearable
              w={260}
            />
          ) : (
            <MultiSelect
              label="Setting"
              placeholder={kinds.length ? undefined : 'Any'}
              data={SETTINGS_KINDS}
              value={kinds}
              onChange={(v) => f.set('kind', joinCsv(v))}
              clearable
              w={220}
            />
          )}
        </ReportFilterBar>
      }
    >
      <ReportProblems rangeError={rangeErr} error={query.error} />
      <Tabs value={tab} onChange={setTab} keepMounted={false}>
        <Tabs.List aria-label="Approval report part" className="no-print">
          <Tabs.Tab value="actions">Case actions</Tabs.Tab>
          <Tabs.Tab value="settings">Settings changes</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="actions" pt="sm">
          <Stack gap="sm">
            {summary && (
              <SimpleGrid cols={{ base: 1, md: 2 }} spacing="sm">
                <Card withBorder padding="sm">
                  <Table aria-label="Decisions by checker" withRowBorders={false}>
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th scope="col">Checker</Table.Th>
                        <Table.Th scope="col" ta="right">
                          Approved
                        </Table.Th>
                        <Table.Th scope="col" ta="right">
                          Rejected
                        </Table.Th>
                        <Table.Th scope="col" ta="right">
                          Self-approved
                        </Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {summary.by_checker.map((c) => (
                        <Table.Tr key={c.checker}>
                          <Table.Td>{c.checker}</Table.Td>
                          <Num>{formatCount(c.approved)}</Num>
                          <Num>{formatCount(c.rejected)}</Num>
                          <Num>{formatCount(c.self_approved)}</Num>
                        </Table.Tr>
                      ))}
                      {summary.by_checker.length === 0 && (
                        <Table.Tr>
                          <Table.Td colSpan={4}>No decision in this period.</Table.Td>
                        </Table.Tr>
                      )}
                    </Table.Tbody>
                  </Table>
                </Card>
                <Card withBorder padding="sm" role="group" aria-label="Pending now">
                  <Text size="sm">
                    Waiting for a checker now (any date): <b>{formatCount(summary.pending_now)}</b>
                  </Text>
                  {summary.oldest_pending && (
                    <Text size="sm">
                      Oldest submitted {formatDateTime(summary.oldest_pending.submitted_at)}
                      {spaLink(summary.oldest_pending.link) && (
                        <>
                          {' - '}
                          <Anchor component={Link} to={spaLink(summary.oldest_pending.link)!}>
                            open the case
                          </Anchor>
                        </>
                      )}
                    </Text>
                  )}
                </Card>
              </SimpleGrid>
            )}
            <Table.ScrollContainer minWidth={1300}>
              <Table striped aria-label="Case action decisions" aria-busy={actions.isFetching}>
                <Table.Thead>
                  <Table.Tr>
                    {[
                      'Case (RRN)',
                      'Action',
                      'Maker',
                      'Checker',
                      'Decision',
                      'Hours to decision',
                      'Done / reference',
                      'Rejection reason',
                    ].map((h) => (
                      <Table.Th scope="col" key={h} ta={h === 'Hours to decision' ? 'right' : undefined}>
                        {h}
                      </Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {actionRows.map((a) => {
                    const to = spaLink(a.link);
                    // The case-level flag, shown on the action the closure settled (owner 2026-11-09).
                    const bySystem = !!a.closed_by_system && SYSTEM_SETTLED.includes(a.decision);
                    return (
                      <Table.Tr key={a.id}>
                        <Table.Td className="wrap-anywhere">
                          {to ? (
                            <Anchor component={Link} to={to}>
                              {a.rrn || `Case ${a.case_id}`}
                            </Anchor>
                          ) : (
                            a.rrn
                          )}
                          <Text size="xs" c="dimmed">
                            {formatDate(a.business_date)}, {a.rule_code || '-'}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          <Text size="xs">{a.action_type_label}</Text>
                          {a.response_code && (
                            <Text size="xs" c="dimmed">
                              {a.response_code}
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td>
                          {a.maker}
                          <Text size="xs" c="dimmed">
                            {formatDateTime(a.submitted_at)}
                          </Text>
                        </Table.Td>
                        <Table.Td>
                          {a.checker ?? (bySystem && a.decided_at ? 'System' : '-')}
                          {a.decided_at && (
                            <Text size="xs" c="dimmed">
                              {formatDateTime(a.decided_at)}
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td>
                          <Group gap={4}>
                            <DecisionBadge value={a.decision} />
                            {a.self_approved && <SelfApprovedBadge />}
                            {bySystem && <ClosedBySystemBadge />}
                          </Group>
                        </Table.Td>
                        <Num>{a.hours_to_decision === null ? '-' : a.hours_to_decision.toFixed(1)}</Num>
                        <Table.Td>
                          <Text size="xs">{a.marked_done_at ? formatDateTime(a.marked_done_at) : '-'}</Text>
                          {a.reference && (
                            <Text size="xs" c="dimmed" className="wrap-anywhere">
                              Ref: {a.reference}
                            </Text>
                          )}
                        </Table.Td>
                        <Table.Td className="wrap-anywhere">
                          <Text size="xs">{a.rejection_reason || '-'}</Text>
                        </Table.Td>
                      </Table.Tr>
                    );
                  })}
                  {actions.data && actionRows.length === 0 && (
                    <Table.Tr>
                      <Table.Td colSpan={8}>No case action decided or submitted in this period.</Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="settings" pt="sm">
          <Table.ScrollContainer minWidth={1100}>
            <Table striped aria-label="Settings change decisions" aria-busy={settings.isFetching}>
              <Table.Thead>
                <Table.Tr>
                  {['Setting', 'Change', 'Proposed by', 'Decided by', 'Decision', 'Remarks'].map((h) => (
                    <Table.Th scope="col" key={h}>
                      {h}
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {settingRows.map((s, i) => {
                  const to = spaLink(s.link);
                  return (
                    <Table.Tr key={`${s.kind}|${s.proposed_at}|${i}`}>
                      <Table.Td>
                        {to ? (
                          <Anchor component={Link} to={to}>
                            {s.kind_label}
                          </Anchor>
                        ) : (
                          s.kind_label
                        )}
                      </Table.Td>
                      <Table.Td className="wrap-anywhere">{s.item_label || '-'}</Table.Td>
                      <Table.Td>
                        {s.proposed_by ?? '-'}
                        <Text size="xs" c="dimmed">
                          {formatDateTime(s.proposed_at)}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        {s.decided_by ?? '-'}
                        {s.decided_at && (
                          <Text size="xs" c="dimmed">
                            {formatDateTime(s.decided_at)}
                          </Text>
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Group gap={4}>
                          <DecisionBadge value={s.decision} />
                          {s.self_approved && <SelfApprovedBadge />}
                        </Group>
                      </Table.Td>
                      <Table.Td className="wrap-anywhere">
                        <Text size="xs">{s.remarks || '-'}</Text>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
                {settings.data && settingRows.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={6}>No settings change decided or proposed in this period.</Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Tabs.Panel>
      </Tabs>
      <div className="no-print">
        <ListPager
          total={pages}
          value={f.pageNo}
          onChange={(n) => f.set('page', String(n))}
          label="Decisions"
        />
      </div>
      {tab === 'settings' && (
        <Alert color="gray" variant="light" className="no-print">
          Versions loaded with the system (no proposer) were never a maker-checker decision and are not
          listed.
        </Alert>
      )}
    </ReportFrame>
  );
}
