/**
 * "Results (all dates)" = daily reconciliation report (contract B1) and
 * "Settlement (all dates)" = settlement report (B2); Phase 6 design 2.1 /
 * 2.2, R3: own entries on the Reports page.
 *
 * Slice 6.1 built these on GET /dates/; slice 6.2 moves them to the report
 * endpoints, which add the whole-range totals (and month subtotals), the
 * results per outcome and rule x date counts, the SOP figures and the
 * self-check, the "open cases only" / settlement status filters and the
 * download. One row per business date; a click on a row opens that date.
 * 50 dates a page (owner D5).
 */
import { Anchor, Checkbox, Group, MultiSelect, Select, Table, Text } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import type { MouseEvent, ReactNode } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';

import { getReconciliationReport, getSettlementReport, reportPaths } from '../api/client';
import type {
  ExportBlock,
  ReconciliationFigures,
  ReconciliationRow,
  ReportBlock,
  SettlementReportRow,
} from '../api/types';
import { useMe } from '../auth/auth';
import { RunStatusBadge, SettlementStatusBadge, StatusBadge } from '../components/Badges';
import { DetailsSection } from '../components/DetailsSection';
import { ListPager } from '../components/ListPager';
import { ExportButton } from '../features/reports/ExportButton';
import { ReportFilterBar, useReportRange } from '../features/reports/ReportFilterBar';
import { ReportFrame } from '../features/reports/ReportFrame';
import { joinCsv, NoReportAccess, Num, ReportProblems, splitCsv } from '../features/reports/reportParts';
import { labelOf, useMeta, useModuleCode } from '../hooks/module';
import { formatAmount, formatCount, formatDate, formatDateTime } from '../lib/format';
import { spaLink } from '../lib/links';
import { allows } from '../lib/roles';
import { pageBounds } from '../lib/urlFilters';

/** Dates per page (owner D5: 50; the server allows up to 92 = a whole range). */
export const ALL_DATES_PAGE_SIZE = 50;

const ORDERINGS = [
  { value: '', label: 'Newest first' },
  { value: 'business_date', label: 'Oldest first' },
];

/** Range, ordering, page and the report's own filters, all from the address. */
function useDateReportParams(extraKeys: readonly string[]) {
  const module = useModuleCode();
  const { range, error, params } = useReportRange();
  const [, setParams] = useSearchParams();
  const ordering = params.get('ordering') === 'business_date' ? 'business_date' : '';
  const pageNo = Math.max(1, Math.floor(Number(params.get('page') || '1')) || 1);
  const extra: Record<string, string | undefined> = Object.fromEntries(
    extraKeys.map((k) => [k, params.get(k) || undefined]),
  );
  const filters: { page: number; page_size: number } & Record<string, string | number | undefined> = {
    date_from: range?.date_from,
    date_to: range?.date_to,
    ordering: ordering || undefined,
    page: pageNo,
    page_size: ALL_DATES_PAGE_SIZE,
    ...extra,
  };
  const set = (key: string, value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== 'page') next.delete('page');
      return next;
    });
  return { module, range, rangeErr: error, ordering, pageNo, filters, set };
}

type Params = ReturnType<typeof useDateReportParams>;

/** One of the report's own filters, as text. */
const extra = (p: Params, key: string) => {
  const v = p.filters[key];
  return typeof v === 'string' ? v : undefined;
};

/**
 * Click anywhere on a row to open the date's screen; the date link in the
 * first cell is the keyboard / screen-reader way to the same place.
 */
function useRowOpen() {
  const navigate = useNavigate();
  return (to: string) => (e: MouseEvent<HTMLTableRowElement>) => {
    if ((e.target as HTMLElement).closest('a, button, input')) return;
    navigate(to);
  };
}

function Frame({
  title,
  description,
  p,
  count,
  query,
  report,
  exportInfo,
  exportPath,
  extraFilters,
  filterLines = [],
  children,
}: {
  title: string;
  description: string;
  p: Params;
  count: number;
  query: {
    dataUpdatedAt: number;
    isFetching: boolean;
    isPending: boolean;
    error: unknown;
    refetch: () => unknown;
  };
  report: ReportBlock | undefined;
  exportInfo: ExportBlock | undefined;
  exportPath: string;
  extraFilters?: ReactNode;
  filterLines?: string[];
  children: ReactNode;
}) {
  const { pages } = pageBounds(count, p.pageNo, ALL_DATES_PAGE_SIZE);
  return (
    <ReportFrame
      title={title}
      description={description}
      range={p.range}
      report={report}
      filterLines={[...filterLines, `Order: ${p.ordering ? 'oldest first' : 'newest first'}`]}
      updatedAt={query.dataUpdatedAt}
      pageLine={pages > 1 ? `Page ${p.pageNo} of ${pages}` : undefined}
      onRefresh={() => void query.refetch()}
      refreshing={query.isFetching}
      actions={
        <ExportButton
          info={exportInfo}
          path={exportPath}
          query={p.filters}
          fallbackName={`${report?.key ?? 'report'}_${p.range?.date_from}_to_${p.range?.date_to}`}
        />
      }
      filters={
        <ReportFilterBar>
          {extraFilters}
          <Select
            label="Order"
            data={ORDERINGS}
            value={p.ordering}
            onChange={(v) => p.set('ordering', v ?? '')}
            allowDeselect={false}
            w={150}
          />
        </ReportFilterBar>
      }
    >
      <ReportProblems rangeError={p.rangeErr} error={query.error} />
      {p.range && !query.error && (
        <Text size="sm" aria-live="polite">
          {query.isPending ? 'Loading...' : `${formatCount(count)} business date(s) in the range`}
        </Text>
      )}
      {children}
      <div className="no-print">
        <ListPager total={pages} value={p.pageNo} onChange={(n) => p.set('page', String(n))} label="Dates" />
      </div>
    </ReportFrame>
  );
}

const EMPTY = 'No business dates in this range. Try "Last month" or wider dates.';

// ---------------------------------------------------------------------------
// Results (all dates) = daily reconciliation report (B1)
// ---------------------------------------------------------------------------
/** Where a row opens: the date page, at its results step when there are results. */
export const resultsHref = (r: Pick<ReconciliationRow, 'links'>) => {
  const date = spaLink(r.links.date) ?? '/';
  return r.links.results ? `${date}#step-results` : date;
};

const GROUP_STATUSES = ['MATCHED', 'UNMATCHED', 'PENDING_RULE_EVALUATION'] as const;
const GROUP_STATUS_TEXT: Record<string, string> = {
  MATCHED: 'Matched',
  UNMATCHED: 'Unmatched',
  PENDING_RULE_EVALUATION: 'Awaiting rules',
};

/** The number cells shared by a date row, a month subtotal and the range total. */
function FigureCells({ f }: { f: ReconciliationFigures }) {
  const g = f.match_groups;
  return (
    <>
      <Num>
        {formatCount(f.files.present)} of {formatCount(f.files.expected)}
      </Num>
      <Num>{formatCount(f.files.missing)}</Num>
      <Num>{formatCount(g.total)}</Num>
      {GROUP_STATUSES.map((s) => (
        <Num key={s}>{formatCount(g.by_status[s] ?? 0)}</Num>
      ))}
      <Num>
        {formatCount(g.on_us)} / {formatCount(g.cbs_reversed)}
      </Num>
      <Num>{formatCount(f.cases.created)}</Num>
      <Num>{formatCount(f.cases.open)}</Num>
    </>
  );
}

export function ResultsAllDatesPage() {
  const allowed = allows(useMe().permissions, 'reports.view');
  const p = useDateReportParams(['open_cases_only']);
  const { data: meta } = useMeta(p.module);
  const open = useRowOpen();
  const query = useQuery({
    queryKey: ['report', 'reconciliation', p.module, p.filters],
    queryFn: () => getReconciliationReport(p.module, p.filters),
    placeholderData: keepPreviousData,
    enabled: allowed && !!p.range,
  });
  if (!allowed) return <NoReportAccess title="Results (all dates)" what="the reports" />;
  const data = query.data;
  const rows = data?.results ?? [];
  const t = data?.totals;
  const openOnly = extra(p, 'open_cases_only') === 'true';
  // Outcomes with a result anywhere in the range (all of them are 0-filled by the server).
  const categories = t
    ? Object.keys(t.results_by_category).filter((c) => (t.results_by_category[c] ?? 0) > 0)
    : [];
  const ruleCodes = data?.rule_codes ?? [];
  const cols = 12;

  return (
    <Frame
      title="Results (all dates)"
      description="Daily reconciliation report: one row per business date. Click a row to open that date's results."
      p={p}
      count={data?.count ?? 0}
      query={query}
      report={data?.report}
      exportInfo={data?.export}
      exportPath={reportPaths.reconciliation(p.module)}
      filterLines={openOnly ? ['Only dates with open cases'] : []}
      extraFilters={
        <Checkbox
          label="Only dates with open cases"
          checked={openOnly}
          onChange={(e) => p.set('open_cases_only', e.currentTarget.checked ? 'true' : '')}
          mb={8}
        />
      }
    >
      {p.range && (
        <Table.ScrollContainer minWidth={1300}>
          <Table aria-label="Results by business date" aria-busy={query.isFetching} highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Business date</Table.Th>
                <Table.Th scope="col" ta="right">
                  Files present
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Missing files
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Transactions
                </Table.Th>
                {GROUP_STATUSES.map((s) => (
                  <Table.Th scope="col" ta="right" key={s}>
                    {GROUP_STATUS_TEXT[s]}
                  </Table.Th>
                ))}
                <Table.Th scope="col" ta="right">
                  On-us / CBS reversed
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Cases created
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Cases open
                </Table.Th>
                <Table.Th scope="col">Settlement</Table.Th>
                <Table.Th scope="col">Rules and last run</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((r) => {
                const to = resultsHref(r);
                return (
                  <Table.Tr key={r.business_date} onClick={open(to)} style={{ cursor: 'pointer' }}>
                    <Table.Td>
                      <Anchor
                        component={Link}
                        to={to}
                        aria-label={`${r.links.results ? 'Results' : 'Overview'} for ${formatDate(r.business_date)}`}
                      >
                        {formatDate(r.business_date)}
                      </Anchor>
                    </Table.Td>
                    <FigureCells f={r} />
                    <Table.Td>
                      <SettlementStatusBadge value={r.settlement?.status} />
                      {r.settlement?.status === 'UNEXPLAINED' && (
                        <Text size="xs">{formatAmount(r.settlement.unexplained_amount)}</Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      <Text size="xs">
                        {r.rule_evaluation
                          ? `${r.rule_evaluation.rule_set}, ${formatDateTime(r.rule_evaluation.finished_at)}`
                          : 'Rules not run'}
                      </Text>
                      {r.last_run ? (
                        <Group gap={4} wrap="nowrap">
                          <RunStatusBadge value={r.last_run.status} />
                          <Text size="xs">by {r.last_run.requested_by || '-'}</Text>
                        </Group>
                      ) : (
                        <Text size="xs" c="dimmed">
                          No run
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
              {!query.isPending && !query.error && rows.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={cols}>{EMPTY}</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
            {t && rows.length > 0 && (
              <Table.Tfoot>
                {(data?.months ?? []).map((m) => (
                  <Table.Tr key={m.month}>
                    <Table.Th scope="row">Total {m.month}</Table.Th>
                    <FigureCells f={m} />
                    <Table.Td colSpan={2} />
                  </Table.Tr>
                ))}
                <Table.Tr fw={600}>
                  <Table.Th scope="row">Total, whole range ({formatCount(t.dates)} dates)</Table.Th>
                  <FigureCells f={t} />
                  <Table.Td colSpan={2}>
                    <Text size="xs">
                      Unexplained settlement: {formatAmount(t.settlement.unexplained_amount)}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              </Table.Tfoot>
            )}
          </Table>
        </Table.ScrollContainer>
      )}

      {t && rows.length > 0 && (
        <DetailsSection title="Results by outcome and by rule" id="results-breakdown-title">
          <Table.ScrollContainer minWidth={600}>
            <Table withTableBorder aria-label="Results by outcome, by date">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Business date</Table.Th>
                  {categories.map((c) => (
                    <Table.Th scope="col" ta="right" key={c}>
                      {labelOf(meta?.status_categories, c)}
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((r) => (
                  <Table.Tr key={r.business_date}>
                    <Table.Th scope="row">{formatDate(r.business_date)}</Table.Th>
                    {categories.map((c) => (
                      <Num key={c}>{formatCount(r.results_by_category[c] ?? 0)}</Num>
                    ))}
                  </Table.Tr>
                ))}
                <Table.Tr fw={600}>
                  <Table.Th scope="row">Total, whole range</Table.Th>
                  {categories.map((c) => (
                    <Num key={c}>{formatCount(t.results_by_category[c] ?? 0)}</Num>
                  ))}
                </Table.Tr>
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          {ruleCodes.length === 0 ? (
            <Text size="sm">No rule applied to any transaction in this range.</Text>
          ) : (
            <Table.ScrollContainer minWidth={600}>
              <Table withTableBorder aria-label="Results by rule, by date">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th scope="col">Business date</Table.Th>
                    {ruleCodes.map((c) => (
                      <Table.Th
                        scope="col"
                        ta="right"
                        key={c}
                        title={meta?.rules.find((x) => x.code === c)?.name}
                      >
                        {c}
                      </Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {rows.map((r) => (
                    <Table.Tr key={r.business_date}>
                      <Table.Th scope="row">{formatDate(r.business_date)}</Table.Th>
                      {ruleCodes.map((c) => (
                        <Num key={c}>{formatCount(r.rule_counts[c] ?? 0)}</Num>
                      ))}
                    </Table.Tr>
                  ))}
                  <Table.Tr fw={600}>
                    <Table.Th scope="row">Total, whole range</Table.Th>
                    {ruleCodes.map((c) => (
                      <Num key={c}>{formatCount(t.rule_counts[c] ?? 0)}</Num>
                    ))}
                  </Table.Tr>
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          )}
        </DetailsSection>
      )}
    </Frame>
  );
}

// ---------------------------------------------------------------------------
// Settlement (all dates) = settlement report (B2)
// ---------------------------------------------------------------------------
export const settlementHref = (r: Pick<SettlementReportRow, 'links'>) =>
  spaLink(r.links.settlement) ?? spaLink(r.links.date) ?? '/';

export const SETTLEMENT_STATUS_OPTIONS = [
  { value: 'TALLIED', label: 'Tallied' },
  { value: 'UNEXPLAINED', label: 'Unexplained' },
  { value: 'NOT_READY', label: 'Not ready' },
  { value: 'NONE', label: 'Not computed' },
];
const statusText = (s: string) => SETTLEMENT_STATUS_OPTIONS.find((o) => o.value === s)?.label ?? s;

export function SettlementAllDatesPage() {
  const allowed = allows(useMe().permissions, 'reports.view');
  const p = useDateReportParams(['status']);
  const { data: meta } = useMeta(p.module);
  const open = useRowOpen();
  const query = useQuery({
    queryKey: ['report', 'settlement', p.module, p.filters],
    queryFn: () => getSettlementReport(p.module, p.filters),
    placeholderData: keepPreviousData,
    enabled: allowed && !!p.range,
  });
  if (!allowed) return <NoReportAccess title="Settlement (all dates)" what="the reports" />;
  const data = query.data;
  const rows = data?.results ?? [];
  const t = data?.totals;
  const statuses = splitCsv(extra(p, 'status'));

  return (
    <Frame
      title="Settlement (all dates)"
      description="Settlement report: did each date's NPCI settlement tie to CBS. Click a row to open that date's settlement."
      p={p}
      count={data?.count ?? 0}
      query={query}
      report={data?.report}
      exportInfo={data?.export}
      exportPath={reportPaths.settlement(p.module)}
      filterLines={statuses.length ? [`Status: ${statuses.map(statusText).join(', ')}`] : []}
      extraFilters={
        <MultiSelect
          label="Status"
          placeholder={statuses.length ? undefined : 'Any'}
          data={SETTLEMENT_STATUS_OPTIONS}
          value={statuses}
          onChange={(v) => p.set('status', joinCsv(v))}
          clearable
          w={260}
        />
      }
    >
      {t && (
        <Group gap="lg" aria-label="Settlement summary, whole range" role="group">
          {SETTLEMENT_STATUS_OPTIONS.filter((o) => t.by_status[o.value]).map((o) => (
            <Text size="sm" key={o.value}>
              {o.label}: <b>{formatCount(t.by_status[o.value])}</b>
            </Text>
          ))}
          <Text size="sm">
            Unexplained, whole range: <b>{formatAmount(t.unexplained_amount)}</b>
          </Text>
          <Text size="sm">
            Fees net, whole range: <b>{formatAmount(t.fees_net)}</b>
          </Text>
        </Group>
      )}
      {p.range && (
        <Table.ScrollContainer minWidth={1300}>
          <Table aria-label="Settlement by business date" aria-busy={query.isFetching} highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Business date</Table.Th>
                <Table.Th scope="col">Status</Table.Th>
                <Table.Th scope="col">NTSL self-check</Table.Th>
                <Table.Th scope="col" ta="right">
                  SOP CBS net
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  SOP NTSL net
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  SOP difference
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Unexplained amount
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Fees net
                </Table.Th>
                <Table.Th scope="col">Reasons</Table.Th>
                <Table.Th scope="col">Settlement case</Table.Th>
                <Table.Th scope="col">Computed</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((r) => {
                const to = settlementHref(r);
                const caseLink = spaLink(r.settlement_case?.link);
                return (
                  <Table.Tr key={r.business_date} onClick={open(to)} style={{ cursor: 'pointer' }}>
                    <Table.Td>
                      <Anchor
                        component={Link}
                        to={to}
                        aria-label={`${r.links.settlement ? 'Settlement' : 'Overview'} for ${formatDate(r.business_date)}`}
                      >
                        {formatDate(r.business_date)}
                      </Anchor>
                    </Table.Td>
                    <Table.Td>
                      <SettlementStatusBadge value={r.status} />
                    </Table.Td>
                    <Table.Td>
                      {r.self_check.cycles_total
                        ? `${formatCount(r.self_check.cycles_pass)} of ${formatCount(r.self_check.cycles_total)} cycles pass`
                        : '-'}
                    </Table.Td>
                    <Num>{formatAmount(r.sop?.cbs_net)}</Num>
                    <Num>{formatAmount(r.sop?.ntsl_net)}</Num>
                    <Num>{formatAmount(r.sop?.difference)}</Num>
                    <Num>
                      {formatAmount(r.unexplained_amount)}
                      {r.unexplained_count ? (
                        <Text size="xs" c="dimmed">
                          {formatCount(r.unexplained_count)} item(s)
                        </Text>
                      ) : null}
                    </Num>
                    <Num>{formatAmount(r.fees_net)}</Num>
                    <Table.Td className="wrap-anywhere">
                      {r.reasons.length ? r.reasons.join('; ') : '-'}
                    </Table.Td>
                    <Table.Td>
                      {r.settlement_case && caseLink ? (
                        <Anchor
                          component={Link}
                          to={caseLink}
                          aria-label={`Settlement case ${r.settlement_case.id}`}
                        >
                          <StatusBadge value={r.settlement_case.status} choices={meta?.case_statuses} />
                        </Anchor>
                      ) : (
                        '-'
                      )}
                    </Table.Td>
                    <Table.Td>
                      {formatDateTime(r.computed_at)}
                      {(r.tolerance_version || r.ntsl_mapping_version) && (
                        <Text size="xs" c="dimmed">
                          {[
                            r.ntsl_mapping_version && `Mapping ${r.ntsl_mapping_version}`,
                            r.tolerance_version && `Tolerance ${r.tolerance_version}`,
                          ]
                            .filter(Boolean)
                            .join(', ')}
                        </Text>
                      )}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
              {!query.isPending && !query.error && rows.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={11}>{EMPTY}</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Frame>
  );
}
