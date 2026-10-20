/**
 * Match group / presence detail (design 4.13): one RRN on one date - each
 * leg with its source row, the overlay rows (DRC / Timeout / Adjustment),
 * the flags, the current rule result, earlier superseded results, and the
 * linked case.
 */
import {
  Alert,
  Anchor,
  Badge,
  Card,
  Group,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router';

import { ApiError, getGroup } from '../api/client';
import type { FactInfo, FullRuleResult, ModuleMeta } from '../api/types';
import { PriorityBadge, StatusBadge } from '../components/Badges';
import { Field } from '../components/Field';
import { OverlayRowsTable } from '../components/OverlayRowsTable';
import { labelOf, useMeta, useModuleCode } from '../hooks/module';
import { displayValue, formatDate, formatDateTime, formatRupees, yesNo } from '../lib/format';

function FactCell({ module, fact }: { module: string; fact: FactInfo | null }) {
  if (!fact) return <>-</>;
  return (
    <>
      <Text size="sm">
        {fact.register_type}
        {fact.settlement_cycle ? ` ${fact.settlement_cycle}` : ''}, row {fact.row_reference || '-'}
      </Text>
      {fact.uploaded_file_id && (
        <Anchor
          component={Link}
          to={`/${module}/files/${fact.uploaded_file_id}`}
          size="xs"
          className="wrap-anywhere"
        >
          {fact.filename ?? `file ${fact.uploaded_file_id}`}
        </Anchor>
      )}
    </>
  );
}

function due(r: FullRuleResult) {
  if (!r.due_date) return r.tat_unresolved ? 'unresolved' : '-';
  return `${formatDate(r.due_date)}${r.due_cutoff_time ? ` ${r.due_cutoff_time.slice(0, 5)}` : ''}`;
}

function RuleResultCard({ r, meta }: { r: FullRuleResult; meta: ModuleMeta | undefined }) {
  return (
    <Card withBorder padding="sm">
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Field label="Rule">
          {r.rule_code} - {r.rule_name}
        </Field>
        <Field label="Rule set">{r.rule_set}</Field>
        <Field label="Status category">{labelOf(meta?.status_categories, r.status_category)}</Field>
        <Field label="Priority">
          <PriorityBadge value={r.priority} choices={meta?.priorities} />
        </Field>
        <Field label="Action">{r.action || '-'}</Field>
        <Field label="Response code">{r.response_code ?? '-'}</Field>
        <Field label="Deadline (TAT)">{r.tat_label || '-'}</Field>
        <Field label="Due">{due(r)}</Field>
        <Field label="Evaluated">{formatDateTime(r.evaluated_at)}</Field>
        <Field label="Calendar incomplete">{yesNo(r.calendar_incomplete)}</Field>
      </SimpleGrid>
      <Text size="sm" mt="xs">
        <strong>Reason:</strong> {r.reason || '-'}
      </Text>
      {Object.keys(r.facts).length > 0 && (
        <Table withTableBorder aria-label="Facts the current rule used" maw={700} mt="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Fact</Table.Th>
              <Table.Th scope="col">Value</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {Object.entries(r.facts).map(([k, v]) => (
              <Table.Tr key={k}>
                <Table.Td>{k}</Table.Td>
                <Table.Td className="wrap-anywhere">{displayValue(v)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Card>
  );
}

export function GroupDetailPage() {
  const module = useModuleCode();
  const params = useParams();
  const businessDate = params.businessDate ?? '';
  const id = Number(params.groupId);
  const { data: meta } = useMeta(module);
  const valid = /^\d{4}-\d{2}-\d{2}$/.test(businessDate) && Number.isInteger(id) && id > 0;

  const query = useQuery({
    queryKey: ['group', module, businessDate, id],
    queryFn: () => getGroup(module, businessDate, id),
    enabled: valid,
  });
  const g = query.data;

  if (!valid) return <Alert color="red">Invalid match group address.</Alert>;
  if (query.isPending) return <Skeleton h={300} />;
  if (query.error || !g) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Alert
        color="red"
        role="alert"
        title={notFound ? 'Match group not found' : 'The match group could not be loaded'}
      >
        {notFound ? `There is no match group ${id} on ${formatDate(businessDate)}.` : query.error?.message}
      </Alert>
    );
  }

  const flags: [string, boolean | null][] = [
    ['Amount match', g.flags.amount_match],
    ['Direction match', g.flags.direction_match],
    ['Status match', g.flags.status_match],
    ['CBS reversed', g.flags.cbs_reversed],
    ['On-us', g.flags.is_on_us],
  ];

  return (
    <Stack>
      <Anchor component={Link} to={`/${module}/dates/${businessDate}/results`} size="sm">
        Back to the results of {formatDate(businessDate)}
      </Anchor>
      <Title order={1} size="h3" className="wrap-anywhere">
        Match group {g.rrn}
      </Title>
      {g.flags.is_superseded && (
        <Alert color="gray" title="Superseded">
          This group was replaced by a later presence-matrix build on {formatDateTime(g.superseded_at)}.
        </Alert>
      )}
      <SimpleGrid cols={{ base: 2, md: 4 }} spacing="sm">
        <Field label="Business date">{formatDate(g.business_date)}</Field>
        <Field label="Cycle">{g.settlement_cycle ?? '-'}</Field>
        <Field label="Match status">{labelOf(meta?.match_group_statuses, g.processing_status)}</Field>
        <Field label="Case">
          {g.case ? (
            <Group gap={4}>
              <Anchor component={Link} to={`/${module}/cases/${g.case.id}`}>
                Open case #{g.case.id}
              </Anchor>
              <StatusBadge value={g.case.status} choices={meta?.case_statuses} />
              <Text size="xs">held by {g.case.assigned_to ?? 'nobody'}</Text>
            </Group>
          ) : (
            'no case'
          )}
        </Field>
      </SimpleGrid>
      <Group gap={6} aria-label="Match flags" role="group">
        {flags.map(([label, v]) => (
          <Badge key={label} variant="outline" color={v === false ? 'red' : 'gray'}>
            {label}: {yesNo(v)}
          </Badge>
        ))}
      </Group>

      <Stack gap="xs" component="section" aria-labelledby="legs-title">
        <Title order={2} size="h4" id="legs-title">
          Legs
        </Title>
        <Table.ScrollContainer minWidth={1000}>
          <Table withTableBorder aria-label="Legs">
            <Table.Thead>
              <Table.Tr>
                {[
                  'Leg',
                  'Present',
                  'Amount',
                  'Direction',
                  'Response code',
                  'SSK side / dispute',
                  'Source row',
                  'Paired row',
                ].map((h) => (
                  <Table.Th key={h} scope="col">
                    {h}
                  </Table.Th>
                ))}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {g.legs.map((l) => (
                <Table.Tr key={l.leg}>
                  <Table.Td>
                    {l.leg}
                    {l.kind === 'overlay' && (
                      <Text size="xs" c="dimmed">
                        overlay
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>{yesNo(l.present)}</Table.Td>
                  <Table.Td ta="right">{formatRupees(l.amount)}</Table.Td>
                  <Table.Td>{l.direction ?? '-'}</Table.Td>
                  <Table.Td>{l.response_code ?? '-'}</Table.Td>
                  <Table.Td>
                    {[l.ssk_side, l.dispute_type].filter(Boolean).join(' / ') || '-'}
                    {l.original_not_loaded && (
                      <Text size="xs" c="orange">
                        Original transaction not loaded
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <FactCell module={module} fact={l.fact} />
                  </Table.Td>
                  <Table.Td>
                    <FactCell module={module} fact={l.paired_fact} />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Stack>

      <Stack gap="xs" component="section" aria-labelledby="overlay-title">
        <Title order={2} size="h4" id="overlay-title">
          DRC / Timeout / Adjustment rows of this date
        </Title>
        <OverlayRowsTable rows={g.overlay_rows} />
      </Stack>

      <Stack gap="xs" component="section" aria-labelledby="rule-result-title">
        <Title order={2} size="h4" id="rule-result-title">
          Current rule result
        </Title>
        {g.rule_result ? (
          <RuleResultCard r={g.rule_result} meta={meta} />
        ) : (
          <Text size="sm">Not evaluated.</Text>
        )}
      </Stack>

      <Stack gap="xs" component="section" aria-labelledby="earlier-results-title">
        <Title order={2} size="h4" id="earlier-results-title">
          Earlier rule results
        </Title>
        {g.earlier_rule_results.length === 0 ? (
          <Text size="sm">None: this group was evaluated once.</Text>
        ) : (
          <Table.ScrollContainer minWidth={900}>
            <Table withTableBorder aria-label="Earlier rule results">
              <Table.Thead>
                <Table.Tr>
                  {[
                    'Evaluated',
                    'Superseded',
                    'Rule',
                    'Rule set',
                    'Status category',
                    'Priority',
                    'Reason',
                  ].map((h) => (
                    <Table.Th key={h} scope="col">
                      {h}
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {g.earlier_rule_results.map((r) => (
                  <Table.Tr key={r.id}>
                    <Table.Td>{formatDateTime(r.evaluated_at)}</Table.Td>
                    <Table.Td>{formatDateTime(r.superseded_at)}</Table.Td>
                    <Table.Td>{r.rule_code}</Table.Td>
                    <Table.Td>{r.rule_set}</Table.Td>
                    <Table.Td>{labelOf(meta?.status_categories, r.status_category)}</Table.Td>
                    <Table.Td>{labelOf(meta?.priorities, r.priority)}</Table.Td>
                    <Table.Td className="wrap-anywhere">{r.reason || '-'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Stack>
    </Stack>
  );
}
