/**
 * The frame every report screen shares (design 5.3, section 2 "figures as of"):
 * title, "Figures as of <time>", Refresh and Print, and the print-only header.
 *
 * Print layout (app.css @media print): the menu, header, filter inputs and
 * buttons are hidden; a header block prints the bank, module, report name,
 * filters, page and who printed it when. "Print -> Save as PDF" in the
 * browser is the PDF route (R1: no PDF files from the server).
 */
import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { useEffect, useState, type ReactNode } from 'react';

import { useMe } from '../../auth/auth';
import { BRAND } from '../../config';
import { formatDate, formatDateTime } from '../../lib/format';
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
  children: ReactNode;
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
  children,
}: ReportFrameProps) {
  const me = useMe();
  const moduleName = useModuleName();
  const asOf = updatedAt ? formatDateTime(new Date(updatedAt).toISOString()) : null;
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
        <Text fw={700}>
          {BRAND.bankName} - {BRAND.appName}
        </Text>
        <Text fw={700} size="lg">
          {title}
        </Text>
        <Text size="sm">Module: {moduleName}</Text>
        <Text size="sm">Business dates: {rangeLabel(range)}</Text>
        {filterLines.map((l) => (
          <Text size="sm" key={l}>
            {l}
          </Text>
        ))}
        {pageLine && <Text size="sm">{pageLine} (only this page is printed)</Text>}
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
      </Text>
      {children}
    </Stack>
  );
}
