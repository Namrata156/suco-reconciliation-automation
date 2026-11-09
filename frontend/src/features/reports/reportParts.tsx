/**
 * Small pieces the Phase 6 report screens share: the "cannot load" alerts,
 * the no-access notice, comma-list filters and right-aligned number cells.
 */
import { Alert, Table, type TableTdProps } from '@mantine/core';
import type { ReactNode } from 'react';

import { refusalText } from './ExportButton';

/** "R01,R13" -> ["R01", "R13"]; empty / missing -> []. */
export const splitCsv = (v: string | null | undefined): string[] =>
  (v ?? '')
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);

/** ["R01", "R13"] -> "R01,R13"; [] -> "" (the filter is removed from the address). */
export const joinCsv = (values: readonly string[]) => values.join(',');

/** A number cell: right-aligned, so figures line up. */
export function Num({ children, ...rest }: TableTdProps & { children?: ReactNode }) {
  return (
    <Table.Td ta="right" {...rest}>
      {children}
    </Table.Td>
  );
}

export function NoReportAccess({ title, what = 'this report' }: { title: string; what?: string }) {
  return (
    <Alert color="yellow" title={title} role="alert">
      Your role does not include {what}.
    </Alert>
  );
}

/** The range error from the address (nothing is loaded) and the server's refusal, if any. */
export function ReportProblems({ rangeError, error }: { rangeError?: string | null; error?: unknown }) {
  return (
    <>
      {rangeError && (
        <Alert color="red" role="alert" title="These dates cannot be used">
          {rangeError} Pick the dates again above.
        </Alert>
      )}
      {error ? (
        <Alert color="red" role="alert" title="The report could not be loaded">
          {refusalText(error)}
        </Alert>
      ) : null}
    </>
  );
}

/** Yes / No for a filter select ("" = any). */
export const ANY_YES_NO = (yes: string, no: string) => [
  { value: '', label: 'Any' },
  { value: 'true', label: yes },
  { value: 'false', label: no },
];
