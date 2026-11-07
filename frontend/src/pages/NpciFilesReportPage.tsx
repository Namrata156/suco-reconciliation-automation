/**
 * "NPCI files (SSKUPI)" report (UI refinements U5, 8.1; contract F1): every
 * SSKUPI file generated for the business dates of a range (_2 files
 * included), with rows, amount, who generated it and when, and who downloaded
 * it and when. Totals are for the whole range. Download buttons only where the
 * server gives a link (Maker, Checker, Operations User); each download is
 * logged by the server. The Excel download comes with the shared Phase 6
 * export ("export" is null today), so there is no button for it yet.
 */
import { Alert, Anchor, Badge, Button, Select, Table, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';

import { ApiError, downloadNpciFile, getNpciFilesReport } from '../api/client';
import type { NpciFilesReportRow } from '../api/types';
import { useMe } from '../auth/auth';
import { ListPager } from '../components/ListPager';
import { saveBlob } from '../features/npci/NpciFilePanel';
import { ReportFilterBar, useReportRange } from '../features/reports/ReportFilterBar';
import { ReportFrame } from '../features/reports/ReportFrame';
import { useModuleCode } from '../hooks/module';
import { formatCount, formatDate, formatDateTime, formatRupees } from '../lib/format';
import { spaLink } from '../lib/links';
import { allows } from '../lib/roles';
import { pageBounds } from '../lib/urlFilters';

export const NPCI_REPORT_PAGE_SIZE = 50;

const DOWNLOADED = [
  { value: '', label: 'Any' },
  { value: 'true', label: 'Downloaded' },
  { value: 'false', label: 'Never downloaded' },
];
const ORDERINGS = [
  { value: '', label: 'Newest first' },
  { value: 'business_date', label: 'Oldest first' },
];

/** A refusal as one text (the range rules answer 400 with field messages). */
const refusal = (e: unknown) =>
  e instanceof ApiError
    ? [e.detail, ...Object.values(e.fieldErrors).flat()].join(' ')
    : 'The server could not be reached.';

export function NpciFilesReportPage() {
  const me = useMe();
  const module = useModuleCode();
  const qc = useQueryClient();
  const allowed = allows(me.permissions, 'reports.view');
  const { range, error: rangeErr, params } = useReportRange();
  const [, setParams] = useSearchParams();
  const downloaded = ['true', 'false'].includes(params.get('downloaded') ?? '')
    ? params.get('downloaded')!
    : '';
  const ordering = params.get('ordering') === 'business_date' ? 'business_date' : '';
  const pageNo = Math.max(1, Math.floor(Number(params.get('page') || '1')) || 1);
  const [downloading, setDownloading] = useState<number | null>(null);

  const filters = {
    date_from: range?.date_from,
    date_to: range?.date_to,
    downloaded: downloaded || undefined,
    ordering: ordering || undefined,
    page: pageNo,
    page_size: NPCI_REPORT_PAGE_SIZE,
  };
  const query = useQuery({
    queryKey: ['report', 'npci-files', module, filters],
    queryFn: () => getNpciFilesReport(module, filters),
    placeholderData: keepPreviousData,
    enabled: allowed && !!range,
  });
  const set = (key: 'downloaded' | 'ordering' | 'page', value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      if (key !== 'page') next.delete('page');
      return next;
    });

  const download = async (f: NpciFilesReportRow) => {
    if (!f.download_url) return;
    setDownloading(f.id);
    try {
      saveBlob(await downloadNpciFile(f.download_url), f.file_name);
    } catch (e) {
      notifications.show({
        color: 'red',
        title: `${f.file_name} could not be downloaded`,
        message: refusal(e),
      });
    } finally {
      setDownloading(null);
      void qc.invalidateQueries({ queryKey: ['report', 'npci-files', module] });
      void qc.invalidateQueries({ queryKey: ['npci-files', module] });
    }
  };

  if (!allowed) {
    return (
      <Alert color="yellow" title="NPCI files (SSKUPI)" role="alert">
        Your role does not include the reports.
      </Alert>
    );
  }

  const data = query.data;
  const rows = data?.results ?? [];
  const { pages } = pageBounds(data?.count ?? 0, pageNo, NPCI_REPORT_PAGE_SIZE);
  const t = data?.totals;

  return (
    <ReportFrame
      title="NPCI files (SSKUPI)"
      description="Every SSKUPI file generated for the business dates, with who generated and downloaded it."
      range={range}
      filterLines={[
        `Downloaded: ${DOWNLOADED.find((d) => d.value === downloaded)?.label ?? 'Any'}`,
        `Order: ${ordering ? 'oldest first' : 'newest first'}`,
      ]}
      updatedAt={query.dataUpdatedAt}
      pageLine={pages > 1 ? `Page ${pageNo} of ${pages}` : undefined}
      onRefresh={() => void query.refetch()}
      refreshing={query.isFetching}
      filters={
        <ReportFilterBar>
          <Select
            label="Downloaded"
            data={DOWNLOADED}
            value={downloaded}
            onChange={(v) => set('downloaded', v ?? '')}
            allowDeselect={false}
            w={170}
          />
          <Select
            label="Order"
            data={ORDERINGS}
            value={ordering}
            onChange={(v) => set('ordering', v ?? '')}
            allowDeselect={false}
            w={150}
          />
        </ReportFilterBar>
      }
    >
      {rangeErr && (
        <Alert color="red" role="alert" title="These dates cannot be used">
          {rangeErr} Pick the dates again above.
        </Alert>
      )}
      {query.error && (
        <Alert color="red" role="alert" title="The report could not be loaded">
          {refusal(query.error)}
        </Alert>
      )}
      {t && (
        <Text size="sm" fw={600} role="group" aria-label="Totals for the range">
          {formatCount(t.files)} file(s), {formatCount(t.rows)} row(s), {formatRupees(t.amount)}.{' '}
          {formatCount(t.downloaded)} downloaded, {formatCount(t.not_downloaded)} never downloaded.
        </Text>
      )}
      <Table.ScrollContainer minWidth={1100}>
        <Table withTableBorder striped aria-label="NPCI files (SSKUPI)" aria-busy={query.isFetching}>
          <Table.Thead>
            <Table.Tr>
              {['Business date', 'File', 'Rows', 'Amount', 'Generated', 'Downloads', ''].map((h, i) => (
                <Table.Th key={i} scope="col" ta={h === 'Rows' || h === 'Amount' ? 'right' : undefined}>
                  {h}
                </Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((f) => {
              const dateLink = spaLink(f.links?.date) ?? `/${module}/dates/${f.business_date}`;
              const n = f.download_count ?? f.downloads?.length ?? 0;
              const last = f.downloads?.[0];
              return (
                <Table.Tr key={f.id}>
                  <Table.Td>
                    <Anchor component={Link} to={`${dateLink}#step-sskupi`}>
                      {formatDate(f.business_date)}
                    </Anchor>
                  </Table.Td>
                  <Table.Td className="wrap-anywhere">
                    <Text size="sm" fw={600}>
                      {f.file_name}
                    </Text>
                    {f.partial && (
                      <Badge size="sm" variant="light" color="orange">
                        Partial: {formatCount(f.not_included_count)} left out
                      </Badge>
                    )}
                    {f.remarks && (
                      <Text size="xs" c="dimmed">
                        {f.remarks}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td ta="right">{formatCount(f.row_count)}</Table.Td>
                  <Table.Td ta="right">{formatRupees(f.total_amount)}</Table.Td>
                  <Table.Td>
                    {f.generated_by || '-'}
                    <Text size="xs" c="dimmed">
                      {formatDateTime(f.generated_at)}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    {n === 0 ? (
                      <Text size="sm" c="orange" fw={600}>
                        Never downloaded
                      </Text>
                    ) : (
                      <>
                        <Text size="sm">{n === 1 ? 'Once' : `${formatCount(n)} times`}</Text>
                        {last && (
                          <Text size="xs" c="dimmed">
                            Last: {last.by || '-'}, {formatDateTime(last.at)}
                          </Text>
                        )}
                      </>
                    )}
                  </Table.Td>
                  <Table.Td className="no-print">
                    {f.download_url && (
                      <Button
                        size="xs"
                        onClick={() => void download(f)}
                        loading={downloading === f.id}
                        aria-label={`Download ${f.file_name}`}
                      >
                        Download
                      </Button>
                    )}
                  </Table.Td>
                </Table.Tr>
              );
            })}
            {!query.isPending && !query.error && rows.length === 0 && range && (
              <Table.Tr>
                <Table.Td colSpan={7}>No SSKUPI file was generated for these business dates.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      <div className="no-print">
        <ListPager total={pages} value={pageNo} onChange={(p) => set('page', String(p))} label="NPCI files" />
      </div>
    </ReportFrame>
  );
}
