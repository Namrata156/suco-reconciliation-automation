/**
 * Daily / monthly transaction volume (Phase 6 design 2.3, contract B3): the
 * rows and amounts each source file carried, per day or per month, by file
 * type and optionally by cycle.
 *
 * As built (accepted by the PM 2026-11-09):
 *  - sides differ per file type: NPCI files Acquirer / Issuer, CBS Debit /
 *    Credit (plus "Direction unknown" for rows with no direction), and DRC,
 *    Timeout and Adjustment a single "All rows";
 *  - NPCI approved / declined only for Raw Data, Merchant and BBP; a dash
 *    for the others;
 *  - dates with files but no volume figures come back as "missing_dates"
 *    and are shown as a notice (not a refusal).
 * The TD 2% / BD 8% rates are not shown (R11).
 */
import { Alert, Checkbox, MultiSelect, SegmentedControl, Select, Stack, Table, Text } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router';

import { getVolumeReport, reportPaths } from '../api/client';
import type { VolumeFigures } from '../api/types';
import { useMe } from '../auth/auth';
import { ListPager } from '../components/ListPager';
import { ExportButton } from '../features/reports/ExportButton';
import { ReportFilterBar, useReportRange } from '../features/reports/ReportFilterBar';
import { ReportFrame } from '../features/reports/ReportFrame';
import { joinCsv, NoReportAccess, Num, ReportProblems, splitCsv } from '../features/reports/reportParts';
import { useMeta, useModuleCode } from '../hooks/module';
import { formatAmount, formatCount, formatDate } from '../lib/format';
import { MAX_RANGE_MONTHS } from '../lib/reports';
import { allows } from '../lib/roles';
import { pageBounds } from '../lib/urlFilters';

export const VOLUME_PAGE_SIZE = 50;

/** The volume summary has no NTSL figures; the meta lists the NTSL file type too. */
const NOT_IN_VOLUME = new Set(['NTSL']);

/** "2026-07" -> "Jul-2026"; a day as 14-Jul-2026. */
export function periodLabel(period: string): string {
  if (/^\d{4}-\d{2}$/.test(period)) return formatDate(`${period}-01`).slice(3);
  return formatDate(period);
}

/** The sides of one row, one line each ("Acquirer: 21,718 / 5,46,13,083.81"). */
function Sides({ f }: { f: VolumeFigures }) {
  return (
    <Stack gap={0}>
      {f.sides.map((s) => (
        <Text size="xs" key={s.side}>
          {s.side_label}: {formatCount(s.count)} / {formatAmount(s.amount)}
        </Text>
      ))}
    </Stack>
  );
}

function NpciCells({ f }: { f: VolumeFigures }) {
  return (
    <>
      <Num>{f.npci ? formatCount(f.npci.approved) : '-'}</Num>
      <Num>{f.npci ? formatCount(f.npci.declined) : '-'}</Num>
    </>
  );
}

export function VolumeReportPage() {
  const allowed = allows(useMe().permissions, 'reports.view');
  const module = useModuleCode();
  const { data: meta } = useMeta(module);
  const [params, setParams] = useSearchParams();
  const byMonth = params.get('group_by') === 'month';
  const { range, error: rangeErr } = useReportRange(byMonth ? { maxMonths: MAX_RANGE_MONTHS } : {});
  const byCycle = params.get('by_cycle') === 'true';
  const fileTypes = splitCsv(params.get('file_type'));
  const oldestFirst = params.get('ordering') === 'period';
  const pageNo = Math.max(1, Math.floor(Number(params.get('page') || '1')) || 1);
  const filters = {
    date_from: range?.date_from,
    date_to: range?.date_to,
    group_by: byMonth ? 'month' : undefined,
    file_type: fileTypes.length ? joinCsv(fileTypes) : undefined,
    by_cycle: byCycle ? 'true' : undefined,
    ordering: oldestFirst ? 'period' : undefined,
    page: pageNo,
    page_size: VOLUME_PAGE_SIZE,
  };
  const query = useQuery({
    queryKey: ['report', 'volume', module, filters],
    queryFn: () => getVolumeReport(module, filters),
    placeholderData: keepPreviousData,
    enabled: allowed && !!range,
  });
  const set = (key: string, value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== 'page') next.delete('page');
      return next;
    });

  if (!allowed) return <NoReportAccess title="Daily / monthly transaction volume" what="the reports" />;
  const data = query.data;
  const rows = data?.results ?? [];
  const { pages } = pageBounds(data?.count ?? 0, pageNo, VOLUME_PAGE_SIZE);
  // File type names: the report's own labels where known, else the code.
  const labels = new Map<string, string>();
  for (const f of [...(data?.totals ?? []), ...rows]) labels.set(f.file_type, f.file_type_label);
  const typeOptions = (meta?.file_types ?? [])
    .filter((f) => !NOT_IN_VOLUME.has(f.code))
    .map((f) => ({ value: f.code, label: labels.get(f.code) ?? f.code }));
  const cols = byCycle ? 8 : 7;

  return (
    <ReportFrame
      title="Daily / monthly transaction volume"
      description="Rows and amounts each source file carried, per day or per month."
      range={range}
      report={data?.report}
      filterLines={[
        `Per ${byMonth ? 'month' : 'day'}${byCycle ? ', by cycle' : ''}`,
        `File types: ${fileTypes.length ? fileTypes.map((c) => labels.get(c) ?? c).join(', ') : 'all'}`,
        `Order: ${oldestFirst ? 'oldest first' : 'newest first'}`,
      ]}
      updatedAt={query.dataUpdatedAt}
      pageLine={pages > 1 ? `Page ${pageNo} of ${pages}` : undefined}
      onRefresh={() => void query.refetch()}
      refreshing={query.isFetching}
      actions={
        <ExportButton
          info={data?.export}
          path={reportPaths.volume(module)}
          query={filters}
          fallbackName={`volume_${range?.date_from}_to_${range?.date_to}`}
        />
      }
      filters={
        <ReportFilterBar maxMonths={byMonth ? MAX_RANGE_MONTHS : undefined}>
          <SegmentedControl
            aria-label="Per day or per month"
            data={[
              { value: 'day', label: 'Per day' },
              { value: 'month', label: 'Per month' },
            ]}
            value={byMonth ? 'month' : 'day'}
            onChange={(v) => set('group_by', v === 'month' ? 'month' : '')}
          />
          <MultiSelect
            label="File types"
            placeholder={fileTypes.length ? undefined : 'All'}
            data={typeOptions}
            value={fileTypes}
            onChange={(v) => set('file_type', joinCsv(v))}
            clearable
            w={280}
          />
          <Checkbox
            label="By cycle"
            checked={byCycle}
            onChange={(e) => set('by_cycle', e.currentTarget.checked ? 'true' : '')}
            mb={8}
          />
          <Select
            label="Order"
            data={[
              { value: '', label: 'Newest first' },
              { value: 'period', label: 'Oldest first' },
            ]}
            value={oldestFirst ? 'period' : ''}
            onChange={(v) => set('ordering', v ?? '')}
            allowDeselect={false}
            w={150}
          />
        </ReportFilterBar>
      }
    >
      {byMonth && (
        <Text size="sm" c="dimmed" className="no-print">
          Per month, the dates are widened to whole months (at most {MAX_RANGE_MONTHS}).
        </Text>
      )}
      <ReportProblems rangeError={rangeErr} error={query.error} />
      {data && data.missing_dates.length > 0 && (
        <Alert color="yellow" role="status" title="Some dates have no volume figures yet">
          No volume figures for {data.missing_dates.map(formatDate).join(', ')}: those dates are not counted
          below. The figures are made when a date is run (or with the fill-in command, by IT).
        </Alert>
      )}

      {data && data.totals.length > 0 && (
        <Table.ScrollContainer minWidth={800}>
          <Table withTableBorder aria-label="Volume totals per file type, whole range">
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">File type (whole range)</Table.Th>
                <Table.Th scope="col">By side: rows / amount</Table.Th>
                <Table.Th scope="col" ta="right">
                  Rows
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Amount
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  NPCI approved
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  NPCI declined
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.totals.map((f) => (
                <Table.Tr key={f.file_type}>
                  <Table.Th scope="row">{f.file_type_label}</Table.Th>
                  <Table.Td>
                    <Sides f={f} />
                  </Table.Td>
                  <Num>{formatCount(f.total_count)}</Num>
                  <Num>{formatAmount(f.total_amount)}</Num>
                  <NpciCells f={f} />
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      {range && (
        <Table.ScrollContainer minWidth={900}>
          <Table striped aria-label="Volume by period and file type" aria-busy={query.isFetching}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">{byMonth ? 'Month' : 'Business date'}</Table.Th>
                <Table.Th scope="col">File type</Table.Th>
                {byCycle && <Table.Th scope="col">Cycle</Table.Th>}
                <Table.Th scope="col">By side: rows / amount</Table.Th>
                <Table.Th scope="col" ta="right">
                  Rows
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Amount
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  NPCI approved
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  NPCI declined
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((r) => (
                <Table.Tr key={`${r.period}|${r.file_type}|${r.cycle ?? ''}`}>
                  <Table.Td>{periodLabel(r.period)}</Table.Td>
                  <Table.Td>{r.file_type_label}</Table.Td>
                  {byCycle && <Table.Td>{r.cycle ?? '-'}</Table.Td>}
                  <Table.Td>
                    <Sides f={r} />
                  </Table.Td>
                  <Num>{formatCount(r.total_count)}</Num>
                  <Num>{formatAmount(r.total_amount)}</Num>
                  <NpciCells f={r} />
                </Table.Tr>
              ))}
              {!query.isPending && !query.error && rows.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={cols}>No volume figures for these dates and file types.</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
      <div className="no-print">
        <ListPager total={pages} value={pageNo} onChange={(n) => set('page', String(n))} label="Volume" />
      </div>
    </ReportFrame>
  );
}
