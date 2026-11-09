/**
 * The download buttons every report shares (contract section A, RR3).
 *
 *  - One button per format the server lists in export.formats: "Download
 *    CSV" today; "Download Excel" appears by itself once IT approves openpyxl
 *    and the server starts listing "xlsx". Nothing is assumed beyond the list.
 *  - A CSV holds one sheet; a report with several sheets gets a sheet picker
 *    next to the button (default: the tab on screen, else the first sheet).
 *  - Over the row limit the button is disabled and says why, so a refused
 *    download is never started (the server refuses it too, with 400).
 *  - The file is fetched with the session (api/client.ts downloadReport), so
 *    a refusal shows as a message, never as a JSON page; the server logs
 *    every successful download in the audit trail (REPORT_EXPORTED).
 */
import { Button, Group, Select, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useState } from 'react';

import { ApiError, downloadReport } from '../../api/client';
import type { ExportBlock } from '../../api/types';
import { saveBlob } from '../npci/NpciFilePanel';
import { formatCount } from '../../lib/format';

type QueryValue = string | number | boolean | undefined | null;

const FORMAT_LABEL: Record<string, string> = { csv: 'CSV', xlsx: 'Excel' };
/** Excel first when the server offers it (one file with every sheet). */
const FORMAT_ORDER = ['xlsx', 'csv'];

/** A refusal as one text (the server's sentence plus its field messages). */
export const refusalText = (e: unknown) =>
  e instanceof ApiError
    ? [...new Set([e.detail, ...Object.values(e.fieldErrors).flat()])].join(' ')
    : 'The server could not be reached.';

export interface ExportButtonProps {
  /** The report's "export" block; nothing is shown without it. */
  info: ExportBlock | null | undefined;
  /** The report's address (api/client.ts reportPaths). */
  path: string;
  /** The filters on screen; paging is dropped (a download is never paged). */
  query: Record<string, QueryValue>;
  /** The CSV sheet to offer first (e.g. the tab on screen). */
  defaultSheet?: string;
  /** Used when the server sends no file name. */
  fallbackName: string;
  /** Names the buttons for screen readers when a screen has more than one report. */
  label?: string;
}

export function ExportButton({ info, path, query, defaultSheet, fallbackName, label }: ExportButtonProps) {
  const sheets = info?.sheets ?? [];
  const [sheet, setSheet] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (!info) return null;
  const formats = FORMAT_ORDER.filter((f) => info.formats.includes(f));
  if (formats.length === 0) return null;

  const chosen =
    sheets.find((s) => s.key === sheet) ?? sheets.find((s) => s.key === defaultSheet) ?? sheets[0];
  const tooBig = (fmt: string) =>
    fmt === 'csv' ? (chosen?.row_count ?? info.row_count) > info.limit : !info.allowed;
  const anyTooBig = formats.some(tooBig);

  const run = async (fmt: string) => {
    setBusy(fmt);
    try {
      const rest = Object.fromEntries(
        Object.entries(query).filter(([k]) => k !== 'page' && k !== 'page_size'),
      ) as Record<string, QueryValue>;
      const { blob, fileName } = await downloadReport(
        path,
        { ...rest, export: fmt, sheet: fmt === 'csv' && sheets.length > 1 ? chosen?.key : undefined },
        `${fallbackName}.${fmt}`,
      );
      saveBlob(blob, fileName);
    } catch (e) {
      notifications.show({
        color: 'red',
        title: 'The report could not be downloaded',
        message: refusalText(e),
      });
    } finally {
      setBusy(null);
    }
  };

  const suffix = label ? ` (${label})` : '';
  return (
    <Group gap="xs" className="no-print" align="flex-end" wrap="nowrap">
      {formats.includes('csv') && sheets.length > 1 && (
        <Select
          label="CSV sheet"
          size="xs"
          data={sheets.map((s) => ({ value: s.key, label: `${s.label} (${formatCount(s.row_count)} rows)` }))}
          value={chosen?.key ?? null}
          onChange={setSheet}
          allowDeselect={false}
          w={220}
        />
      )}
      {formats.map((fmt) => (
        <Button
          key={fmt}
          variant="default"
          onClick={() => void run(fmt)}
          loading={busy === fmt}
          disabled={tooBig(fmt) || (busy !== null && busy !== fmt)}
          aria-label={`Download ${FORMAT_LABEL[fmt] ?? fmt}${suffix}`}
        >
          Download {FORMAT_LABEL[fmt] ?? fmt}
        </Button>
      ))}
      {anyTooBig && (
        <Text size="xs" c="red" maw={260}>
          More than the {formatCount(info.limit)} rows a download may hold; narrow the filters.
        </Text>
      )}
    </Group>
  );
}
