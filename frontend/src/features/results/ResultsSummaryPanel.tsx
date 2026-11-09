/**
 * Reconciliation results summary on the business-date overview (design
 * 4.12, first part): counts per rule and outcome (status category) from the
 * latest rule evaluation run, plus the transaction (match group) totals. The paged results list (~70,000 rows a date) is gone (UI
 * refinements U3, 4.5): one transaction is found with "Find an RRN" in the
 * header, so the counts here are plain numbers.
 *
 * UI refinements 2026-10-27 (3.5, 4.4): only the rules that occurred that
 * day are listed, plain name first; ops' words ("Transactions", "Outcome",
 * "Replaced by a later run"). The rule set and evaluation time are in the
 * date page's collapsed "Details".
 *
 * Phase 6 B8: the whole results list of the date as a file (CSV until IT
 * approves Excel; the formats come from GET /reports/meta/). The row count
 * is the summary's transaction total (replaced rows are left out, as in
 * the download).
 */
import { Alert, Card, Group, SimpleGrid, Skeleton, Stack, Table, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { ApiError, getReportsMeta, getResultSummary, reportPaths } from '../../api/client';
import type { ExportBlock, ResultSummary } from '../../api/types';
import { useMe } from '../../auth/auth';
import { allows } from '../../lib/roles';
import { ExportButton } from '../reports/ExportButton';
import { labelOf, useMeta } from '../../hooks/module';
import { formatCount, formatDate } from '../../lib/format';

/** The loaded summary; reads meta (labels) only once there is something to show. */
function SummaryBody({
  module,
  businessDate,
  s,
}: {
  module: string;
  businessDate: string;
  s: ResultSummary;
}) {
  const { data: meta } = useMeta(module);
  // Only the rules that occurred that day (3.5); the rule document lists them all.
  const rules = s.rules.filter((r) => r.count > 0);
  return (
    <Stack gap="xs">
      <SimpleGrid cols={{ base: 2, md: 3 }} spacing="sm">
        <Card withBorder padding="sm">
          <Text size="sm" c="dimmed">
            Transactions
          </Text>
          <Text fw={700}>{formatCount(s.match_groups.total)}</Text>
          <Text size="xs">
            {formatCount(s.match_groups.superseded)} replaced by a later run (not counted)
          </Text>
        </Card>
        <Card withBorder padding="sm">
          <Text size="sm" c="dimmed">
            On-us / CBS reversed
          </Text>
          <Text fw={700}>
            {formatCount(s.match_groups.on_us)} / {formatCount(s.match_groups.cbs_reversed)}
          </Text>
        </Card>
        <Card withBorder padding="sm">
          <Text size="sm" c="dimmed">
            By outcome
          </Text>
          {s.by_status_category.map((x) => (
            <Text size="xs" key={x.value}>
              {x.label}: {formatCount(x.count)}
            </Text>
          ))}
        </Card>
      </SimpleGrid>
      {rules.length === 0 ? (
        <Text size="sm">No rule applied to any transaction on this date.</Text>
      ) : (
        <Table.ScrollContainer minWidth={600}>
          <Table withTableBorder aria-label={`Results per rule for ${formatDate(businessDate)}`}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Rule</Table.Th>
                <Table.Th scope="col">Outcome</Table.Th>
                <Table.Th scope="col" ta="right">
                  Results
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rules.map((r) => (
                <Table.Tr key={r.rule_code}>
                  <Table.Td>
                    {r.name || r.rule_code}
                    {r.name && (
                      <Text size="xs" c="dimmed" span>
                        {' '}
                        {r.rule_code}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>{labelOf(meta?.status_categories, r.status_category)}</Table.Td>
                  <Table.Td ta="right">{formatCount(r.count)}</Table.Td>
                </Table.Tr>
              ))}
              <Table.Tr>
                <Table.Td colSpan={2} fw={700}>
                  Total
                </Table.Td>
                <Table.Td ta="right" fw={700}>
                  {formatCount(s.results_total)}
                </Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Stack>
  );
}

/** What a download of the date's results would hold (RR3 shape, from B0 and the summary). */
export function resultsExportInfo(
  formats: string[],
  limit: number,
  rows: number,
): ExportBlock {
  return {
    formats,
    row_count: rows,
    limit,
    allowed: rows <= limit,
    sheets: [{ key: 'results', label: 'Results', row_count: rows }],
  };
}

function ResultsDownload({ module, businessDate, rows }: { module: string; businessDate: string; rows: number }) {
  const allowed = allows(useMe().permissions, 'reports.view');
  const { data: meta } = useQuery({
    queryKey: ['reports', 'meta'],
    queryFn: getReportsMeta,
    staleTime: 10 * 60_000,
    enabled: allowed,
  });
  if (!allowed || !meta) return null;
  return (
    <ExportButton
      info={resultsExportInfo(meta.export_formats, meta.max_export_rows, rows)}
      path={reportPaths.results(module, businessDate)}
      query={{}}
      fallbackName={`${module.toUpperCase()}_results_${businessDate}`}
      label={`all results of ${formatDate(businessDate)}`}
    />
  );
}

export function ResultsSummaryPanel({
  module,
  businessDate,
  runId,
  enabled,
}: {
  module: string;
  businessDate: string;
  /** The date's latest rule run: a new run refetches the summary. */
  runId: number | null;
  /** False while there is no matrix and no rule run (the server would answer 404). */
  enabled: boolean;
}) {
  const query = useQuery({
    queryKey: ['results-summary', module, businessDate, runId],
    queryFn: () => getResultSummary(module, businessDate),
    enabled,
  });
  const s = query.data;

  let body;
  if (!enabled || (query.error instanceof ApiError && query.error.status === 404)) {
    body = <Text size="sm">No reconciliation results for this date yet. They appear after a run.</Text>;
  } else if (query.isPending) {
    body = <Skeleton h={120} />;
  } else if (query.error || !s) {
    body = (
      <Alert color="red" role="alert" title="The results summary could not be loaded">
        {query.error?.message}
      </Alert>
    );
  } else {
    body = <SummaryBody module={module} businessDate={businessDate} s={s} />;
  }

  return (
    <Stack gap="xs" component="section" aria-labelledby="results-summary-title">
      <Group justify="space-between" align="flex-end">
        <Title order={2} size="h4" id="results-summary-title">
          Reconciliation results
        </Title>
        {s && <ResultsDownload module={module} businessDate={businessDate} rows={s.match_groups.total} />}
      </Group>
      {body}
    </Stack>
  );
}
