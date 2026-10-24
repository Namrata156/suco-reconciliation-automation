/**
 * "Results (all dates)" and "Settlement (all dates)" (Phase 6 slice 6.1;
 * design 2.1 / 2.2, R3: own menu entries, owner 2026-10-22).
 *
 * First version on the EXISTING GET /api/v1/<module>/dates/ (date range,
 * ordering, paging; nothing new on the server). Each row opens the existing
 * per-date screen. The full columns (results per category, rule x date,
 * NTSL self-check, SOP figures), whole-range totals, the status / open-cases
 * filters and the download wait for contract B1 / B2 (slice 6.2).
 */
import { Alert, Anchor, Group, Select, Table, Text } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { MouseEvent, ReactNode } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';

import { listDates } from '../api/client';
import type { BusinessDateOverview } from '../api/types';
import { useMe } from '../auth/auth';
import { RunStatusBadge, SettlementStatusBadge } from '../components/Badges';
import { ListPager } from '../components/ListPager';
import { reasonList } from '../features/settlement/SettlementBlock';
import { ReportFilterBar, useReportRange } from '../features/reports/ReportFilterBar';
import { ReportFrame } from '../features/reports/ReportFrame';
import { useModuleCode } from '../hooks/module';
import { formatAmount, formatCount, formatDate, formatDateTime } from '../lib/format';
import { sumAmounts } from '../lib/reports';
import { allows } from '../lib/roles';
import { pageBounds } from '../lib/urlFilters';

/** A month per page (the /dates/ overview counts match groups live per date; design 6). */
export const ALL_DATES_PAGE_SIZE = 31;

const ORDERINGS = [
  { value: '', label: 'Newest first' },
  { value: 'business_date', label: 'Oldest first' },
];

/** The /dates/ rows for the report's range, ordering and page (all from the address). */
function useAllDates(allowed: boolean) {
  const module = useModuleCode();
  const { range, error, params } = useReportRange();
  const [, setParams] = useSearchParams();
  const ordering = params.get('ordering') === 'business_date' ? 'business_date' : '';
  const pageNo = Math.max(1, Math.floor(Number(params.get('page') || '1')) || 1);
  const filters = {
    date_from: range?.date_from,
    date_to: range?.date_to,
    ordering: ordering || undefined,
    page: pageNo,
    page_size: ALL_DATES_PAGE_SIZE,
  };
  const query = useQuery({
    queryKey: ['all-dates', module, filters],
    queryFn: () => listDates(module, filters),
    placeholderData: keepPreviousData,
    enabled: allowed && !!range,
  });
  const set = (key: 'ordering' | 'page', value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== 'page') next.delete('page');
      return next;
    });
  const count = query.data?.count ?? 0;
  const { pages } = pageBounds(count, pageNo, ALL_DATES_PAGE_SIZE);
  return { module, range, rangeErr: error, ordering, pageNo, pages, count, query, set };
}

type AllDates = ReturnType<typeof useAllDates>;

function OrderSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Select
      label="Order"
      data={ORDERINGS}
      value={value}
      onChange={(v) => onChange(v ?? '')}
      allowDeselect={false}
      w={150}
    />
  );
}

/**
 * Click anywhere on a row to open the date's screen; the date link in the
 * first cell is the keyboard / screen-reader way to the same place.
 */
function useRowOpen() {
  const navigate = useNavigate();
  return (to: string) => (e: MouseEvent<HTMLTableRowElement>) => {
    if ((e.target as HTMLElement).closest('a, button')) return;
    navigate(to);
  };
}

function NoAccess({ title }: { title: string }) {
  return (
    <Alert color="yellow" title={title} role="alert">
      Your role does not include the reconciliation results.
    </Alert>
  );
}

function Frame({
  title,
  description,
  d,
  children,
}: {
  title: string;
  description: string;
  d: AllDates;
  children: ReactNode;
}) {
  return (
    <ReportFrame
      title={title}
      description={description}
      range={d.range}
      filterLines={[`Order: ${d.ordering ? 'oldest first' : 'newest first'}`]}
      updatedAt={d.query.dataUpdatedAt}
      pageLine={d.pages > 1 ? `Page ${d.pageNo} of ${d.pages}` : undefined}
      onRefresh={() => void d.query.refetch()}
      refreshing={d.query.isFetching}
      filters={
        <ReportFilterBar>
          <OrderSelect value={d.ordering} onChange={(v) => d.set('ordering', v)} />
        </ReportFilterBar>
      }
    >
      {d.rangeErr && (
        <Alert color="red" role="alert" title="These dates cannot be used">
          {d.rangeErr} Pick the dates again above.
        </Alert>
      )}
      {d.query.error && (
        <Alert color="red" role="alert" title="The business dates could not be loaded">
          {d.query.error.message}
        </Alert>
      )}
      {d.range && (
        <Text size="sm" aria-live="polite">
          {d.query.isPending ? 'Loading...' : `${formatCount(d.count)} business date(s) in the range`}
        </Text>
      )}
      {children}
      <div className="no-print">
        <ListPager
          total={d.pages}
          value={d.pageNo}
          onChange={(p) => d.set('page', String(p))}
          label="Dates"
        />
      </div>
    </ReportFrame>
  );
}

const EMPTY = 'No business dates in this range. Try "Last month" or wider dates.';

// ---------------------------------------------------------------------------
// Results (all dates) = daily reconciliation report (design 2.1)
// ---------------------------------------------------------------------------
/** Where a row opens: the date's results when there are any, else the date's overview. */
export const resultsHref = (module: string, o: BusinessDateOverview) =>
  o.matrix.built || o.rule_evaluation
    ? `/${module}/dates/${o.business_date}/results`
    : `/${module}/dates/${o.business_date}`;

export function ResultsAllDatesPage() {
  const allowed = allows(useMe().permissions, 'results.view');
  const d = useAllDates(allowed);
  const open = useRowOpen();
  if (!allowed) return <NoAccess title="Results (all dates)" />;
  const rows = d.query.data?.results ?? [];
  const sum = (f: (o: BusinessDateOverview) => number) => rows.reduce((t, o) => t + f(o), 0);

  return (
    <Frame
      title="Results (all dates)"
      description="Daily reconciliation report: one row per business date. Click a row to open that date's results."
      d={d}
    >
      {d.range && (
        <Table.ScrollContainer minWidth={1100}>
          <Table aria-label="Results by business date" aria-busy={d.query.isFetching} highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Business date</Table.Th>
                <Table.Th scope="col">Expected files present</Table.Th>
                <Table.Th scope="col">Missing files</Table.Th>
                <Table.Th scope="col">Match groups</Table.Th>
                <Table.Th scope="col">Rule evaluation</Table.Th>
                <Table.Th scope="col">Cases created</Table.Th>
                <Table.Th scope="col">Cases open</Table.Th>
                <Table.Th scope="col">Settlement</Table.Th>
                <Table.Th scope="col">Last run</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((o) => {
                const to = resultsHref(d.module, o);
                return (
                  <Table.Tr key={o.business_date} onClick={open(to)} style={{ cursor: 'pointer' }}>
                    <Table.Td>
                      <Anchor
                        component={Link}
                        to={to}
                        aria-label={`${o.matrix.built || o.rule_evaluation ? 'Results' : 'Overview'} for ${formatDate(o.business_date)}`}
                      >
                        {formatDate(o.business_date)}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      {o.files.expected - o.files.missing} of {o.files.expected}
                    </Table.Td>
                    <Table.Td>{formatCount(o.files.missing)}</Table.Td>
                    <Table.Td>{o.matrix.built ? formatCount(o.matrix.match_groups) : 'not built'}</Table.Td>
                    <Table.Td>
                      {o.rule_evaluation
                        ? `${o.rule_evaluation.rule_set}, ${formatDateTime(o.rule_evaluation.finished_at ?? o.rule_evaluation.started_at)}, by ${o.rule_evaluation.triggered_by || '-'}`
                        : 'not run'}
                    </Table.Td>
                    <Table.Td>{formatCount(o.cases.total)}</Table.Td>
                    <Table.Td>{formatCount(o.cases.active)}</Table.Td>
                    <Table.Td>
                      <SettlementStatusBadge value={o.settlement?.status} />
                    </Table.Td>
                    <Table.Td>{o.last_run ? <RunStatusBadge value={o.last_run.status} /> : 'none'}</Table.Td>
                  </Table.Tr>
                );
              })}
              {!d.query.isPending && rows.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={9}>{EMPTY}</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
            {rows.length > 1 && (
              <Table.Tfoot>
                <Table.Tr fw={600}>
                  <Table.Th scope="row">Total, dates on this page</Table.Th>
                  <Table.Td />
                  <Table.Td>{formatCount(sum((o) => o.files.missing))}</Table.Td>
                  <Table.Td>{formatCount(sum((o) => o.matrix.match_groups))}</Table.Td>
                  <Table.Td />
                  <Table.Td>{formatCount(sum((o) => o.cases.total))}</Table.Td>
                  <Table.Td>{formatCount(sum((o) => o.cases.active))}</Table.Td>
                  <Table.Td colSpan={2} />
                </Table.Tr>
              </Table.Tfoot>
            )}
          </Table>
        </Table.ScrollContainer>
      )}
    </Frame>
  );
}

// ---------------------------------------------------------------------------
// Settlement (all dates) = settlement report (design 2.2)
// ---------------------------------------------------------------------------
export const settlementHref = (module: string, o: BusinessDateOverview) =>
  o.settlement ? `/${module}/dates/${o.business_date}/settlement` : `/${module}/dates/${o.business_date}`;

const STATUS_ORDER = ['TALLIED', 'UNEXPLAINED', 'NOT_READY', ''] as const;
const STATUS_TEXT: Record<(typeof STATUS_ORDER)[number], string> = {
  TALLIED: 'Tallied',
  UNEXPLAINED: 'Unexplained',
  NOT_READY: 'Not ready',
  '': 'Not computed',
};

/** Dates per settlement status, for the rows on the page ("" = not computed). */
export function settlementCounts(rows: BusinessDateOverview[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const o of rows) {
    const k = o.settlement?.status ?? '';
    out[k] = (out[k] ?? 0) + 1;
  }
  return out;
}

export function SettlementAllDatesPage() {
  const allowed = allows(useMe().permissions, 'results.view');
  const d = useAllDates(allowed);
  const open = useRowOpen();
  if (!allowed) return <NoAccess title="Settlement (all dates)" />;
  const rows = d.query.data?.results ?? [];
  const counts = settlementCounts(rows);
  const unexplained = sumAmounts(rows.map((o) => o.settlement?.unexplained_amount));

  return (
    <Frame
      title="Settlement (all dates)"
      description="Settlement report: did each date's NPCI settlement tie to CBS. Click a row to open that date's settlement."
      d={d}
    >
      {rows.length > 0 && (
        <Group gap="lg" aria-label="Settlement summary, dates on this page" role="group">
          {STATUS_ORDER.filter((s) => counts[s]).map((s) => (
            <Text size="sm" key={s || 'none'}>
              {STATUS_TEXT[s]}: <b>{formatCount(counts[s])}</b>
            </Text>
          ))}
          <Text size="sm">
            Unexplained, dates on this page: <b>{formatAmount(unexplained)}</b>
          </Text>
        </Group>
      )}
      {d.range && (
        <Table.ScrollContainer minWidth={900}>
          <Table aria-label="Settlement by business date" aria-busy={d.query.isFetching} highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Business date</Table.Th>
                <Table.Th scope="col">Status</Table.Th>
                <Table.Th scope="col" style={{ textAlign: 'right' }}>
                  Unexplained amount
                </Table.Th>
                <Table.Th scope="col">Reasons</Table.Th>
                <Table.Th scope="col">Computed at</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((o) => {
                const to = settlementHref(d.module, o);
                const reasons = o.settlement ? reasonList(o.settlement.reasons) : [];
                return (
                  <Table.Tr key={o.business_date} onClick={open(to)} style={{ cursor: 'pointer' }}>
                    <Table.Td>
                      <Anchor
                        component={Link}
                        to={to}
                        aria-label={`${o.settlement ? 'Settlement' : 'Overview'} for ${formatDate(o.business_date)}`}
                      >
                        {formatDate(o.business_date)}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      <SettlementStatusBadge value={o.settlement?.status} />
                    </Table.Td>
                    <Table.Td style={{ textAlign: 'right' }}>
                      {formatAmount(o.settlement?.unexplained_amount)}
                    </Table.Td>
                    <Table.Td className="wrap-anywhere">{reasons.length ? reasons.join('; ') : '-'}</Table.Td>
                    <Table.Td>{formatDateTime(o.settlement?.computed_at)}</Table.Td>
                  </Table.Tr>
                );
              })}
              {!d.query.isPending && rows.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={5}>{EMPTY}</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Frame>
  );
}
