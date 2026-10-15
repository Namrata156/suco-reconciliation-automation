/**
 * Home - "my work" (design 4.3, P4 approved): counts only, each a link into
 * the filtered queue. No charts (Phase 6).
 *
 * The server decides which blocks a user gets (api/views.py SummaryView:
 * Maker / Checker / Operations; Administrator and Auditor get all) and sends
 * with each count the queue filter that reproduces it.
 */
import { Alert, Anchor, Card, SimpleGrid, Skeleton, Stack, Text, Title } from '@mantine/core';
import { Link } from 'react-router';

import type { ModuleSummary, SummaryCount } from '../api/types';
import { useModuleCode, useSummary } from '../hooks/module';
import { formatCount, formatDate } from '../lib/format';

export interface CountTile {
  key: string;
  label: string;
  value: number | null | undefined;
  /** Queue query string the tile links to; null = no queue view. */
  query: string | null;
  note?: string;
}

const MAKER_LABELS: Record<string, string> = {
  high_open: 'HIGH priority, open',
  high_overdue: 'HIGH priority, overdue',
  overdue: 'Overdue',
  due_today: 'Due today',
  approved_waiting: 'Approved, waiting to be marked done',
  mine: 'Assigned to me',
  mine_overdue: 'Assigned to me, overdue',
};
const CHECKER_LABELS: Record<string, string> = {
  awaiting: 'Awaiting approval',
  awaiting_high: 'Awaiting approval - HIGH',
  awaiting_submitted_by_me: 'Awaiting approval - submitted by me',
  needs_review: 'Needs review',
};

export function toQueryString(query: Record<string, string> | null): string | null {
  return query ? new URLSearchParams(query).toString() : null;
}

function tilesOf(
  block: Record<string, SummaryCount> | undefined,
  labels: Record<string, string>,
): CountTile[] {
  if (!block) return [];
  return Object.entries(labels)
    .filter(([key]) => block[key])
    .map(([key, label]) => ({
      key,
      label,
      value: block[key]?.count,
      query: toQueryString(block[key]?.query ?? null),
    }));
}

export function makerTiles(s: ModuleSummary): CountTile[] {
  return tilesOf(s.maker, MAKER_LABELS);
}

export function checkerTiles(s: ModuleSummary): CountTile[] {
  if (!s.checker) return [];
  const { settings_awaiting: sa, ...counts } = s.checker;
  return [
    ...tilesOf(counts, CHECKER_LABELS),
    {
      key: 'settings_awaiting',
      label: 'Settings changes awaiting approval',
      value: sa.response_codes + sa.ntsl_mapping + sa.tolerance,
      query: null,
      note: `Response codes ${sa.response_codes}, NTSL mapping ${sa.ntsl_mapping}, tolerance ${sa.tolerance}. Screens arrive in Slice 3.`,
    },
  ];
}

export function operationsTiles(s: ModuleSummary): CountTile[] {
  const o = s.operations;
  if (!o) return [];
  const on = o.latest_business_date ? ` (${formatDate(o.latest_business_date)})` : '';
  return [
    { key: 'files_received', label: `Files received${on}`, value: o.files_received, query: null },
    { key: 'files_loaded', label: `Files loaded${on}`, value: o.files_loaded, query: null },
    { key: 'files_failed', label: `Files failed${on}`, value: o.files_failed, query: null },
    {
      key: 'files_missing',
      label: `Files missing${on}`,
      value: o.files_missing,
      query: null,
      note: o.files_missing === null ? 'Needs the file checklist (Slice 2)' : undefined,
    },
    {
      key: 'awaiting_date_selection',
      label: 'CBS files awaiting date selection',
      value: o.awaiting_date_selection,
      query: null,
    },
  ];
}

function Tiles({
  title,
  tiles,
  module,
  footer,
}: {
  title: string;
  tiles: CountTile[];
  module: string;
  footer?: string;
}) {
  if (tiles.length === 0) return null;
  const id = `h-${title.replace(/\W+/g, '-').toLowerCase()}`;
  return (
    <section aria-labelledby={id}>
      <Title order={2} size="h4" id={id} mb="xs">
        {title}
      </Title>
      <SimpleGrid cols={{ base: 2, md: 3, lg: 4 }} spacing="sm">
        {tiles.map((t) => (
          <Card key={t.key} withBorder padding="sm">
            <Text size="sm" c="dimmed">
              {t.label}
            </Text>
            {t.query !== null ? (
              <Anchor
                component={Link}
                to={`/${module}/cases?${t.query}`}
                fz={28}
                fw={700}
                aria-label={`${t.label}: ${formatCount(t.value)}. Open the list`}
              >
                {formatCount(t.value)}
              </Anchor>
            ) : (
              <Text fz={28} fw={700}>
                {formatCount(t.value)}
              </Text>
            )}
            {t.note && (
              <Text size="xs" c="dimmed">
                {t.note}
              </Text>
            )}
          </Card>
        ))}
      </SimpleGrid>
      {footer && (
        <Text size="sm" mt={4}>
          {footer}
        </Text>
      )}
    </section>
  );
}

export function HomePage() {
  const module = useModuleCode();
  const { data: s, isPending, error } = useSummary(module);

  return (
    <Stack>
      <Title order={1} size="h3">
        My work{s ? ` - ${formatDate(s.business_date_today)}` : ''}
      </Title>
      {error && (
        <Alert color="red" role="alert" title="Counts could not be loaded">
          {error.message}
        </Alert>
      )}
      {isPending && <Skeleton h={120} />}
      {s && (
        <>
          <Tiles title="Maker work" tiles={makerTiles(s)} module={module} />
          <Tiles title="Checker work" tiles={checkerTiles(s)} module={module} />
          <Tiles
            title="Operations"
            tiles={operationsTiles(s)}
            module={module}
            footer={
              s.operations
                ? `Settlement status${s.operations.latest_business_date ? ` for ${formatDate(s.operations.latest_business_date)}` : ''}: ${s.operations.settlement_status ?? 'not computed yet'}`
                : undefined
            }
          />
        </>
      )}
    </Stack>
  );
}
