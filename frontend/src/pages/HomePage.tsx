/**
 * Home - "my work" (design 4.3, P4 approved): the day's job and the SSKUPI
 * file tile on top (UI refinements U2, 1.1 / 1.2; features/home/TodaysJob),
 * then counts, each a link into the filtered queue. No charts (Phase 6).
 *
 * The server decides which blocks a user gets (api/views.py SummaryView:
 * Maker / Checker / Operations; Administrator and Auditor get all) and sends
 * with each count the queue filter that reproduces it. The Operations counts
 * (Slice 2) link to the files list and the business-date overview instead.
 *
 * The case priority is not shown: the server still sends high_open,
 * high_overdue and awaiting_high, but only the keys listed below become
 * tiles, so those three are left out here.
 */
import { Alert, Anchor, Card, SimpleGrid, Skeleton, Stack, Text, Title } from '@mantine/core';
import { Link } from 'react-router';

import type { ModuleSummary, SummaryCount } from '../api/types';
import { TodaysJob } from '../features/home/TodaysJob';
import { useModuleCode, useSummary } from '../hooks/module';
import { addIsoDays, formatCount, formatDate } from '../lib/format';

export interface CountTile {
  key: string;
  label: string;
  value: number | null | undefined;
  /** Queue query string the tile links to; null = no queue view. */
  query: string | null;
  /** Another screen under /<module>/ the tile links to (files, dates), when not the queue. */
  href?: string;
  note?: string;
}

const MAKER_LABELS: Record<string, string> = {
  overdue: 'Overdue',
  due_today: 'Due today',
  approved_waiting: 'Approved, waiting to be marked done',
  mine: 'Assigned to me',
  mine_overdue: 'Assigned to me, overdue',
};
const CHECKER_LABELS: Record<string, string> = {
  awaiting: 'Awaiting approval',
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
      href: 'approvals',
      note: `Response codes ${sa.response_codes}, NTSL mapping ${sa.ntsl_mapping}, tolerance ${sa.tolerance}.`,
    },
  ];
}

/** Operations counts link to the files list / business-date overview (Slice 2). */
export function operationsTiles(s: ModuleSummary): CountTile[] {
  const o = s.operations;
  if (!o) return [];
  const d = o.latest_business_date;
  const on = d ? ` (${formatDate(d)})` : '';
  const files = (q: Record<string, string>) =>
    `files?${new URLSearchParams(d ? { business_date: d, ...q } : q)}`;
  return [
    {
      key: 'files_received',
      label: `Files received${on}`,
      value: o.files_received,
      query: null,
      href: d ? files({}) : undefined,
    },
    {
      key: 'files_loaded',
      label: `Files loaded${on}`,
      value: o.files_loaded,
      query: null,
      href: d ? files({ status: 'LOADED' }) : undefined,
    },
    {
      key: 'files_failed',
      label: `Files failed${on}`,
      value: o.files_failed,
      query: null,
      href: d ? files({ status: 'VALIDATION_FAILED,FAILED' }) : undefined,
    },
    {
      key: 'files_missing',
      label: `Expected files missing${on}`,
      value: o.files_missing,
      query: null,
      href: d ? `dates/${d}` : undefined,
      note: o.files_missing === null ? 'No business date yet' : undefined,
    },
    {
      key: 'awaiting_date_selection',
      label: 'CBS files awaiting date selection',
      value: o.awaiting_date_selection,
      query: null,
      // Every date: a waiting CBS file has no business date yet.
      href: 'files?status=AWAITING_DATE_SELECTION',
    },
  ];
}

/**
 * A count of 0 is greyed out in place (UI refinements 2026-10-27, 1.3), so
 * the tiles that need work stand out; the tiles keep their positions.
 */
export const isZero = (t: Pick<CountTile, 'value'>) => t.value === 0;

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
          <Card key={t.key} withBorder padding="sm" data-zero={isZero(t) || undefined}>
            <Text size="sm" c="dimmed">
              {t.label}
            </Text>
            {t.query !== null || t.href ? (
              <Anchor
                component={Link}
                to={t.query !== null ? `/${module}/cases?${t.query}` : `/${module}/${t.href}`}
                fz={28}
                fw={isZero(t) ? 400 : 700}
                c={isZero(t) ? 'dimmed' : undefined}
                aria-label={`${t.label}: ${formatCount(t.value)}. Open the list`}
              >
                {formatCount(t.value)}
              </Anchor>
            ) : (
              <Text fz={28} fw={isZero(t) ? 400 : 700} c={isZero(t) ? 'dimmed' : undefined}>
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

/**
 * The business date the home page is about: the latest one that can be
 * complete (calendar day D -> business date D-1). From the summary's
 * business_date; an older server only sends business_date_today, so that
 * minus one day.
 */
export function summaryBusinessDate(s: Pick<ModuleSummary, 'business_date' | 'business_date_today'>): string {
  return s.business_date ?? addIsoDays(s.business_date_today, -1);
}

export function HomePage() {
  const module = useModuleCode();
  const { data: s, isPending, error } = useSummary(module);

  return (
    <Stack>
      <Title order={1} size="h3">
        My work{s ? ` - business date ${formatDate(summaryBusinessDate(s))}` : ''}
      </Title>
      {error && (
        <Alert color="red" role="alert" title="Counts could not be loaded">
          {error.message}
        </Alert>
      )}
      {isPending && <Skeleton h={120} />}
      {s && (
        <>
          {s.day && <TodaysJob module={module} day={s.day} />}
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
