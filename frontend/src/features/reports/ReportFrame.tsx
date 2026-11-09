/**
 * The frame every report screen shares (design 5.3, section 2 "figures as of"):
 * title, "Figures as of <time>", Refresh and Print, and the print-only header.
 *
 * Print layout (app.css @media print): the menu, header, filter inputs and
 * buttons are hidden; a header block prints the bank, module, report name,
 * filters, page and who printed it when. "Print -> Save as PDF" in the
 * browser is the PDF route (R1: no PDF files from the server).
 *
 * Phase 6 (contract RR2 / RR3): with the server's "report" block, "Figures
 * as of" is the server's generated_at and the versions the figures were
 * produced with are listed; `actions` holds the download buttons.
 */
import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { useEffect, useState, type ReactNode } from 'react';

import { useMe } from '../../auth/auth';
import { BRAND } from '../../config';
import { formatDate, formatDateTime } from '../../lib/format';
import type { ReportBlock } from '../../api/types';
import type { DateRange } from '../../lib/reports';
import { useModuleName } from './ReportFilterBar';

export interface ReportFrameProps {
  title: string;
  /** One line under the title. */
  description?: string;
  range: DateRange | null;
  /** Extra filters to print, e.g. ["Order: oldest first"]. */
  filterLines?: string[];
  /** react-query dataUpdatedAt (ms); 0 = not loaded yet. */
  updatedAt: number;
  /** "Page 2 of 3" when the list is paged. */
  pageLine?: string;
  onRefresh?: () => void;
  refreshing?: boolean;
  /** The filter bar (not printed). */
  filters?: ReactNode;
  /** The server's report block (RR2): as-of time and versions. */
  report?: ReportBlock;
  /** Replaces the printed "Business dates: ..." line (the ageing report has no dates). */
  scopeLine?: string;
  /** Replaces the module name (the user activity report covers every module). */
  moduleLabel?: string;
  /** Buttons next to Refresh / Print (downloads). */
  actions?: ReactNode;
  children: ReactNode;
}

/** "Rule set v3.2; NTSL mapping v1; tolerance v1", or "" when none apply. */
export function versionsLine(report: ReportBlock | undefined): string {
  const v = report?.versions;
  if (!v) return '';
  const part = (label: string, list: string[] | undefined) =>
    list && list.length ? `${label} ${list.join(', ')}` : null;
  return [
    part('Rule set', v.rule_sets),
    part('NTSL mapping', v.ntsl_mappings),
    part('Tolerance', v.tolerances),
  ]
    .filter(Boolean)
    .join('; ');
}

export const rangeLabel = (r: DateRange | null) =>
  r ? `${formatDate(r.date_from)} to ${formatDate(r.date_to)}` : '-';

export function ReportFrame({
  title,
  description,
  range,
  filterLines = [],
  updatedAt,
  pageLine,
  onRefresh,
  refreshing,
  filters,
  report,
  scopeLine,
  moduleLabel,
  actions,
  children,
}: ReportFrameProps) {
  const me = useMe();
  const currentModule = useModuleName();
  const moduleName = moduleLabel ?? currentModule;
  const asOf = report?.generated_at
    ? formatDateTime(report.generated_at)
    : updatedAt
      ? formatDateTime(new Date(updatedAt).toISOString())
      : null;
  const versions = versionsLine(report);
  // The print header's time is taken when printing starts, not at page load.
  const [printedAt, setPrintedAt] = useState(() => new Date().toISOString());
  useEffect(() => {
    const stamp = () => setPrintedAt(new Date().toISOString());
    window.addEventListener('beforeprint', stamp);
    return () => window.removeEventListener('beforeprint', stamp);
  }, []);

  return (
    <Stack gap="sm">
      <div className="print-only" data-testid="print-header">
        <Group gap="xs" wrap="nowrap">
          <img src={BRAND.logoIcon} alt={`${BRAND.bankName} logo`} width={32} height={32} />
          <Text fw={700}>
            {BRAND.bankFullName} - {BRAND.appName}
          </Text>
        </Group>
        <Text fw={700} size="lg">
          {title}
        </Text>
        <Text size="sm">Module: {moduleName}</Text>
        <Text size="sm">{scopeLine ?? `Business dates: ${rangeLabel(range)}`}</Text>
        {filterLines.map((l) => (
          <Text size="sm" key={l}>
            {l}
          </Text>
        ))}
        {pageLine && <Text size="sm">{pageLine} (only this page is printed)</Text>}
        {versions && <Text size="sm">Produced with: {versions}</Text>}
        <Text size="sm">
          Figures as of {asOf ?? '-'}; printed by {me.username} at {formatDateTime(printedAt)}
        </Text>
      </div>

      <Group justify="space-between" align="flex-start" className="no-print">
        <div>
          <Title order={1} size="h3">
            {title}
          </Title>
          {description && (
            <Text size="sm" c="dimmed">
              {description}
            </Text>
          )}
        </div>
        <Group gap="xs">
          {actions}
          {onRefresh && (
            <Button variant="default" onClick={onRefresh} loading={refreshing}>
              Refresh
            </Button>
          )}
          <Button variant="default" onClick={() => window.print()}>
            Print
          </Button>
        </Group>
      </Group>
      {filters}
      <Text size="sm" c="dimmed" className="no-print" aria-live="polite">
        {asOf
          ? `Figures as of ${asOf}. Cases and settlement results keep changing after a run; refresh for the latest.`
          : 'Loading...'}
        {versions ? ` Produced with: ${versions}.` : ''}
      </Text>
      {children}
    </Stack>
  );
}
