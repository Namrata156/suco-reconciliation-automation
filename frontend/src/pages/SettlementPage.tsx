/**
 * Settlement result per date and cycle (design 4.14): the current result
 * with its SOP check and cycle x side table, the NTSL self-check per cycle,
 * the linked settlement case, earlier results, and the contributing
 * transactions (4.15). TALLIED dates have no case but are shown the same way.
 * The cycle x side table, the self-check, the versions used and the earlier
 * results are under a collapsed "Details" (UI refinements 2026-10-27, 5.4); a
 * failing self-check is also shown on top.
 *
 * UI refinements U3 (5.1-5.3; contract B3 / B4): the page leads with one plain
 * sentence, the SOP row and the explained amount by type
 * (features/settlement/SettlementSummaryView), and the contributing
 * transactions show the unexplained entries only (every row stays one click
 * away). The full result block (tolerance, fees, cycles) moves into
 * "Details" when the server sends the summary.
 */
import { Alert, Anchor, Card, SimpleGrid, Skeleton, Stack, Table, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';

import { ApiError, getSettlement } from '../api/client';
import { SettlementStatusBadge, StatusBadge } from '../components/Badges';
import { DetailsSection } from '../components/DetailsSection';
import { gstWarningLine, SelfCheckTable, selfCheckLine } from '../features/settlement/SelfCheckTable';
import { SettlementBlock } from '../features/settlement/SettlementBlock';
import { SettlementSummaryView } from '../features/settlement/SettlementSummaryView';
import { ContributingEntries } from '../features/settlement/SettlementTransactions';
import { useMeta, useModuleCode } from '../hooks/module';
import { SHOW_GST_ON_FEES } from '../lib/featureFlags';
import { formatCount, formatDate, formatDateTime, formatRupees } from '../lib/format';

export function SettlementPage() {
  const module = useModuleCode();
  const businessDate = useParams().businessDate ?? '';
  const { data: meta } = useMeta(module);
  const valid = /^\d{4}-\d{2}-\d{2}$/.test(businessDate);

  const query = useQuery({
    queryKey: ['settlement', module, businessDate],
    queryFn: () => getSettlement(module, businessDate),
    enabled: valid,
  });
  const s = query.data;

  if (!valid) return <Alert color="red">Invalid date address.</Alert>;
  const back = (
    <Anchor component={Link} to={`/${module}/dates/${businessDate}`} size="sm">
      Back to the business date
    </Anchor>
  );
  if (query.isPending) return <Skeleton h={300} />;
  if (query.error || !s) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Stack>
        {back}
        <Alert
          color={notFound ? 'gray' : 'red'}
          role="alert"
          title={notFound ? 'No settlement yet' : 'The settlement could not be loaded'}
        >
          {notFound
            ? `No settlement result or NTSL self-check for ${formatDate(businessDate)} yet. They appear after a run.`
            : query.error?.message}
        </Alert>
      </Stack>
    );
  }

  const r = s.result;
  const summary = s.summary ?? null;
  const selfCheckFailed = summary
    ? summary.self_check_failed
    : s.self_check.fail > 0 || s.self_check.missing > 0;
  // GST on fees display hidden by owner 2026-11-06 (lib/featureFlags); backend check still runs.
  const gstWarning = SHOW_GST_ON_FEES ? gstWarningLine(s.self_check) : null;

  return (
    <Stack>
      {back}
      <Title order={1} size="h3">
        Settlement {formatDate(s.business_date)}
      </Title>

      {summary && <SettlementSummaryView s={summary} />}

      {/* The self-check shows on top only when it fails (UI refinements 2026-10-27, 5.4). */}
      {selfCheckFailed && (
        <Alert color="red" role="alert" title="NTSL self-check did not pass">
          Self-check: {selfCheckLine(s.self_check)}. See &quot;NTSL self-check per cycle&quot; under Details
          below.
        </Alert>
      )}

      {/* Fee GST check (owner 2026-11-06): warning only, shown on top because Details starts closed. */}
      {gstWarning && (
        <Alert color="yellow" role="status" title={gstWarning}>
          Warning only: it does not change PASS / FAIL. See &quot;NTSL self-check per cycle&quot; under
          Details below.
        </Alert>
      )}

      {!summary && (
        <SimpleGrid
          cols={{ base: 1, sm: 2 }}
          spacing="sm"
          role="group"
          aria-label="Settlement summary"
          maw={800}
        >
          <Card withBorder padding="sm">
            <Text size="sm" c="dimmed">
              Status
            </Text>
            <SettlementStatusBadge value={r?.status} />
          </Card>
          <Card withBorder padding="sm">
            <Text size="sm" c="dimmed">
              Unexplained difference
            </Text>
            <Text fw={700}>
              {r
                ? `Unexplained: ${formatRupees(r.unexplained_amount)} (${formatCount(r.unexplained_count)} transactions)`
                : 'Unexplained: not computed'}
            </Text>
          </Card>
        </SimpleGrid>
      )}

      <Stack gap="xs" component="section" aria-labelledby="settlement-case-title">
        <Title order={2} size="h4" id="settlement-case-title">
          Settlement case
        </Title>
        {s.case ? (
          <Text size="sm" component="div">
            <Anchor component={Link} to={`/${module}/cases/${s.case.id}`}>
              Open settlement case #{s.case.id}
            </Anchor>{' '}
            <StatusBadge value={s.case.status} choices={meta?.case_statuses} /> held by{' '}
            {s.case.assigned_to ?? 'nobody'}
            {s.case.needs_review ? ' - needs review' : ''}
          </Text>
        ) : (
          <Text size="sm">
            No settlement case for this date{r?.status === 'TALLIED' ? ' (tallied).' : '.'}
          </Text>
        )}
      </Stack>

      {r ? (
        !summary && <SettlementBlock s={r} />
      ) : (
        <Alert color="gray" title="No settlement result">
          The NTSL self-check exists, but no settlement result has been computed for this date yet.
        </Alert>
      )}

      {r && (
        <Stack gap="xs" component="section" aria-labelledby="settlement-tx-title">
          <Title order={2} size="h4" id="settlement-tx-title">
            Unexplained entries{summary ? ` (${formatCount(summary.unexplained_entries)})` : ''}
          </Title>
          <ContributingEntries
            module={module}
            source={{ businessDate: s.business_date }}
            meta={meta}
            idPrefix="settlement"
          />
        </Stack>
      )}

      <DetailsSection title="Details" id="settlement-details-title">
        {r && summary && <SettlementBlock s={r} title="Settlement result in full" />}
        <Text size="sm">
          Versions used:{' '}
          {r
            ? `Tolerance v${r.tolerance_version ?? '-'}, NTSL mapping v${r.map_version ?? '-'}, rule run #${r.rule_run_id ?? '-'}`
            : '-'}
        </Text>
        <Title order={3} size="h5" id="self-check-title">
          NTSL self-check per cycle
        </Title>
        <Text size="sm">Self-check: {selfCheckLine(s.self_check)}</Text>
        <SelfCheckTable check={s.self_check} />
        <Title order={3} size="h5" id="earlier-settlement-title">
          Earlier results
        </Title>
        {s.earlier_results.length === 0 ? (
          <Text size="sm">None: the date was settled once.</Text>
        ) : (
          <Table withTableBorder aria-label="Earlier settlement results" maw={800}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Result</Table.Th>
                <Table.Th scope="col">Status</Table.Th>
                <Table.Th scope="col">Computed</Table.Th>
                <Table.Th scope="col">Replaced by a later run</Table.Th>
                <Table.Th scope="col" ta="right">
                  Unexplained
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {s.earlier_results.map((e) => (
                <Table.Tr key={e.id}>
                  <Table.Td>#{e.id}</Table.Td>
                  <Table.Td>
                    <SettlementStatusBadge value={e.status} />
                  </Table.Td>
                  <Table.Td>{formatDateTime(e.computed_at)}</Table.Td>
                  <Table.Td>{formatDateTime(e.superseded_at)}</Table.Td>
                  <Table.Td ta="right">{formatRupees(e.unexplained_amount)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </DetailsSection>
    </Stack>
  );
}
