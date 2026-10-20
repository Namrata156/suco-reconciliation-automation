/**
 * Reconciliation results summary on the business-date overview (design
 * 4.12, first part): counts per rule, status category and priority from the
 * latest rule evaluation run, plus the match-group totals, each count a link
 * into the paged results list.
 */
import { Alert, Anchor, Card, Group, SimpleGrid, Skeleton, Stack, Table, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';

import { ApiError, getResultSummary } from '../../api/client';
import type { ResultSummary } from '../../api/types';
import { PriorityBadge } from '../../components/Badges';
import { labelOf, useMeta } from '../../hooks/module';
import { formatCount, formatDate, formatDateTime } from '../../lib/format';

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
  const list = `/${module}/dates/${businessDate}/results`;
  return (
    <Stack gap="xs">
      <Group gap="md" wrap="wrap">
        <Anchor component={Link} to={list} fw={600}>
          Open the results list
        </Anchor>
        <Text size="sm">
          {s.rule_evaluation
            ? `Rule set ${s.rule_evaluation.rule_set}, evaluated ${formatDateTime(
                s.rule_evaluation.finished_at ?? s.rule_evaluation.started_at,
              )} by ${s.rule_evaluation.triggered_by || 'system'}`
            : 'Rules not evaluated yet'}
        </Text>
      </Group>
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Card withBorder padding="sm">
          <Text size="sm" c="dimmed">
            Match groups
          </Text>
          <Text fw={700}>{formatCount(s.match_groups.total)}</Text>
          <Text size="xs">{formatCount(s.match_groups.superseded)} superseded (not counted)</Text>
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
            By status category
          </Text>
          {s.by_status_category.map((x) => (
            <Text size="xs" key={x.value}>
              <Anchor
                component={Link}
                to={`${list}?status_category=${x.value}`}
                aria-label={`${x.label}: ${formatCount(x.count)} results`}
              >
                {x.label}: {formatCount(x.count)}
              </Anchor>
            </Text>
          ))}
        </Card>
        <Card withBorder padding="sm">
          <Text size="sm" c="dimmed">
            By priority
          </Text>
          {s.by_priority.map((x) => (
            <Text size="xs" key={x.value}>
              <Anchor
                component={Link}
                to={`${list}?priority=${x.value}`}
                aria-label={`Priority ${x.label}: ${formatCount(x.count)} results`}
              >
                {x.label}: {formatCount(x.count)}
              </Anchor>
            </Text>
          ))}
        </Card>
      </SimpleGrid>
      <Table.ScrollContainer minWidth={700}>
        <Table withTableBorder aria-label={`Results per rule for ${formatDate(businessDate)}`}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Rule</Table.Th>
              <Table.Th scope="col">Name</Table.Th>
              <Table.Th scope="col">Status category</Table.Th>
              <Table.Th scope="col">Priority</Table.Th>
              <Table.Th scope="col" ta="right">
                Results
              </Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {s.rules.map((r) => (
              <Table.Tr key={r.rule_code}>
                <Table.Td>{r.rule_code}</Table.Td>
                <Table.Td>{r.name || '-'}</Table.Td>
                <Table.Td>{labelOf(meta?.status_categories, r.status_category)}</Table.Td>
                <Table.Td>
                  {r.priority ? <PriorityBadge value={r.priority} choices={meta?.priorities} /> : '-'}
                </Table.Td>
                <Table.Td ta="right">
                  {r.count > 0 ? (
                    <Anchor
                      component={Link}
                      to={`${list}?rule_code=${encodeURIComponent(r.rule_code)}`}
                      aria-label={`${r.rule_code}: ${formatCount(r.count)} results`}
                    >
                      {formatCount(r.count)}
                    </Anchor>
                  ) : (
                    '0'
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
            <Table.Tr>
              <Table.Td colSpan={4} fw={700}>
                Total
              </Table.Td>
              <Table.Td ta="right" fw={700}>
                {formatCount(s.results_total)}
              </Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Stack>
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
      <Title order={2} size="h4" id="results-summary-title">
        Reconciliation results
      </Title>
      {body}
    </Stack>
  );
}
