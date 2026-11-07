/**
 * "SSKUPI file" on the business-date page (docs/npci_file_design_draft_v1.txt
 * section 6 + Amendments 1 and 2, A7; owner 2026-10-22/23/24). Called "SSKUPI
 * file" everywhere in the UI since 2026-10-27 (owner). Step 6 of the date
 * page (UI refinements U2): the Generate button and why there is nothing new
 * come first, then the files with Download (a file never downloaded is
 * called out), then what the next file would hold.
 *
 * The app only GENERATES the reply file. It never sends anything to NPCI: the
 * user downloads the file and uploads it to the NPCI portal by hand, so the
 * screen says "included in file SSKUPI...csv", never "sent".
 *
 * Amendment 2 (owner 2026-10-24): the file can be generated right after
 * reconciliation. Each row uses the case's current reply: the Checker-approved
 * one, else the Maker's submitted one, else the rule's suggestion
 * (reply_source APPROVED / SUBMITTED / RULE). Some replies still wait for a
 * Checker (NEEDS_APPROVAL) and a rejected reply stays out (REJECTED).
 *
 * Shows what the NEXT file of the date would hold (counts, where the replies
 * come from, the rows with masked accounts, the cases left out and why, the
 * server's warnings), the Generate button with the partial-file confirmation
 * when something is held back (Checker), and the files
 * already generated for the date with their hash, download history and
 * Download (Maker, Checker, Operations User; Administrator and Auditor see the
 * list only). There is no file-approval step in the app (owner 2026-10-23). The capabilities drive the buttons; the server checks every request.
 */
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Checkbox,
  Drawer,
  Group,
  List,
  Modal,
  Popover,
  Skeleton,
  Stack,
  Table,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router';

import {
  ApiError,
  downloadNpciFile,
  generateNpciFile,
  getNpciFile,
  getNpciFilePreview,
  listNpciFiles,
} from '../../api/client';
import type {
  NpciFileCount,
  NpciFileLine,
  NpciFilePreview,
  NpciFileRow,
  NpciNotIncluded,
} from '../../api/types';
import { useMe } from '../../auth/auth';
import { formatCount, formatDate, formatDateTime, formatRupees } from '../../lib/format';
import { can } from '../../lib/roles';
import { OperationHistory } from '../files/OperationHistory';

const FILES_PAGE_SIZE = 50;

/** Reasons in the order the screen lists them (Amendment 2, B2). */
export const REASON_ORDER = [
  'NEEDS_APPROVAL',
  'REJECTED',
  'NEEDS_REVIEW',
  'NO_NUMERIC_CODE',
  'NO_NPCI_ROW',
  'BAD_SOURCE_DATA',
] as const;

/** The screen's own wording for the Amendment 2 reasons; every other reason shows the server's label. */
const REASON_LABEL: Readonly<Record<string, string>> = {
  NEEDS_APPROVAL: "Needs a Checker's approval first",
  REJECTED: "Rejected by the Checker; waiting for the Maker's new reply",
};

/** The label of a left-out case's reason (the server's for unknown or older reasons). */
export function reasonLabel(n: Pick<NpciNotIncluded, 'reason' | 'reason_label'>): string {
  return REASON_LABEL[n.reason] ?? (n.reason_label || n.reason);
}

/** Where a row's reply came from (Amendment 2). */
const REPLY_SOURCE_ORDER = ['RULE', 'SUBMITTED', 'APPROVED'] as const;
const REPLY_SOURCE_LABEL: Readonly<Record<string, string>> = {
  RULE: "Rule's suggestion",
  SUBMITTED: "Maker's reply (submitted)",
  APPROVED: 'Checker-approved',
};
const REPLY_SOURCE_COUNT_LABEL: Readonly<Record<string, string>> = {
  RULE: 'from the rule',
  SUBMITTED: 'from the Maker',
  APPROVED: 'from the Checker',
};

export function replySourceLabel(source: string | null | undefined): string {
  if (!source) return '-';
  return REPLY_SOURCE_LABEL[source] ?? source;
}

/**
 * "250 from the rule, 40 from the Maker, 11 from the Checker", or null when
 * there is nothing to count (or the server does not send the counts).
 */
export function replySourcesSummary(sources: Partial<Record<string, number>> | undefined): string | null {
  if (!sources) return null;
  const known: readonly string[] = REPLY_SOURCE_ORDER;
  const keys = Object.keys(sources)
    .filter((k) => (sources[k] ?? 0) > 0)
    .sort((a, b) => {
      const ia = known.indexOf(a);
      const ib = known.indexOf(b);
      return (ia < 0 ? known.length : ia) - (ib < 0 ? known.length : ib);
    });
  if (keys.length === 0) return null;
  return keys
    .map((k) => `${formatCount(sources[k])} ${REPLY_SOURCE_COUNT_LABEL[k] ?? `from ${k}`}`)
    .join(', ');
}

/** Counts per reply source from a file's rows (the file details carry no such counts). */
export function countReplySources(rows: Pick<NpciFileLine, 'reply_source'>[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const r of rows) if (r.reply_source) out[r.reply_source] = (out[r.reply_source] ?? 0) + 1;
  return out;
}

/** Which reply the file uses, in plain words (Amendment 2, B2). */
const CURRENT_REPLY_TEXT =
  "The file uses each case's current reply: the Checker-approved one, else the Maker's submitted one, else " +
  "the rule's suggestion. Some replies need a Checker's approval first, and a rejected reply stays out.";

/** "RET 115", or "RET (no numeric code yet)" while the code is unknown. */
export function replyCode(flag: string, code: string | null | undefined): string {
  if (!flag) return '-';
  return code ? `${flag} ${code}` : `${flag} (no numeric code yet)`;
}

/**
 * "4 case(s): Needs a Checker's approval first" per reason, from the
 * not-included list (its labels) and the server's counts.
 */
export function notIncludedSummary(
  p: Pick<NpciFilePreview, 'not_included' | 'not_included_counts'>,
): string[] {
  const label = new Map(p.not_included.map((n) => [n.reason, reasonLabel(n)]));
  const known: readonly string[] = REASON_ORDER;
  const reasons = Object.keys(p.not_included_counts).sort((a, b) => {
    const ia = known.indexOf(a);
    const ib = known.indexOf(b);
    return (ia < 0 ? known.length : ia) - (ib < 0 ? known.length : ib);
  });
  return reasons
    .filter((r) => (p.not_included_counts[r] ?? 0) > 0)
    .map((r) => `${formatCount(p.not_included_counts[r])} case(s): ${label.get(r) ?? REASON_LABEL[r] ?? r}`);
}

/**
 * The plain line when the next file would be empty (owner 2026-10-27: say
 * why there is nothing to generate), e.g. "All cases are already in
 * SSKUPI26092026.csv."
 */
export function nothingNewLine(
  p: Pick<NpciFilePreview, 'already_sent_files' | 'not_included' | 'file_date'>,
): string {
  const files = p.already_sent_files.join(', ');
  const left = p.not_included.length;
  if (files && left === 0) return `All cases are already in ${files}.`;
  if (files) {
    return `Nothing new to include: the cases with a ready reply are already in ${files}; ${formatCount(left)} case(s) are left out until their reason is resolved (listed below).`;
  }
  if (left > 0) {
    return `Nothing can go into a file yet: ${formatCount(left)} case(s) are left out until their reason is resolved (listed below).`;
  }
  return `No reply is waiting to be included in a file for ${formatDate(p.file_date)}.`;
}

/** A 409 on generate that asks for the partial-file confirmation. */
export const needsPartialConfirmation = (e: unknown) =>
  e instanceof ApiError && e.isConflict && 'confirm_partial' in e.fieldErrors;

/** The server's refusal as one text: detail plus every field message. */
const refusalText = (e: unknown) =>
  e instanceof ApiError
    ? [e.detail, ...Object.values(e.fieldErrors).flat()].join(' ')
    : 'The server could not be reached.';

// ---------------------------------------------------------------------------
// tables
// ---------------------------------------------------------------------------
function CountsTable({ counts, label }: { counts: NpciFileCount[]; label: string }) {
  return (
    <Table.ScrollContainer minWidth={600}>
      <Table withTableBorder aria-label={label}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Reply</Table.Th>
            <Table.Th scope="col">Text in the file</Table.Th>
            <Table.Th scope="col" ta="right">
              Rows
            </Table.Th>
            <Table.Th scope="col" ta="right">
              Amount
            </Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {counts.map((c) => (
            <Table.Tr key={`${c.flag}-${c.code}-${c.text}`}>
              <Table.Td>{replyCode(c.flag, c.code)}</Table.Td>
              <Table.Td className="wrap-anywhere">{c.text || '-'}</Table.Td>
              <Table.Td ta="right">{formatCount(c.rows)}</Table.Td>
              <Table.Td ta="right">{formatRupees(c.amount)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function CaseLink({ module, id }: { module: string; id: number }) {
  return (
    <Anchor component={Link} to={`/${module}/cases/${id}`}>
      #{id}
    </Anchor>
  );
}

export function NotIncludedTable({
  module,
  rows,
  label,
}: {
  module: string;
  rows: NpciNotIncluded[];
  label: string;
}) {
  return (
    <Table.ScrollContainer minWidth={900}>
      <Table withTableBorder aria-label={label}>
        <Table.Thead>
          <Table.Tr>
            {['Case', 'RRN', 'Rule', 'Case status', 'Reply', 'Why it is left out'].map((h) => (
              <Table.Th key={h} scope="col">
                {h}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((n) => (
            <Table.Tr key={n.case_id}>
              <Table.Td>
                <CaseLink module={module} id={n.case_id} />
                <Text size="xs" c="dimmed">
                  {formatDate(n.case_business_date)}
                </Text>
              </Table.Td>
              <Table.Td>{n.rrn}</Table.Td>
              <Table.Td>{n.rule_code || '-'}</Table.Td>
              <Table.Td>{n.status}</Table.Td>
              <Table.Td>{replyCode(n.flag, n.code)}</Table.Td>
              <Table.Td className="wrap-anywhere">
                {reasonLabel(n)}
                {n.detail && (
                  <Text size="xs" c="dimmed">
                    {n.detail}
                  </Text>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/** The rows of the next file (preview, with the rule) or of a generated file (details, no rule). */
function FileRowsTable({
  module,
  fileDate,
  rows,
  label,
}: {
  module: string;
  fileDate: string;
  rows: (NpciFileLine & { rule_code?: string })[];
  label: string;
}) {
  const withRule = rows.some((r) => r.rule_code !== undefined);
  const headers = [
    'Line',
    'Case',
    'RRN',
    ...(withRule ? ['Rule'] : []),
    'Reply',
    'Reply from',
    'Shtdat',
    'Amount',
    'Account',
    'Text',
  ];
  return (
    <Table.ScrollContainer minWidth={1100}>
      <Table withTableBorder aria-label={label}>
        <Table.Thead>
          <Table.Tr>
            {headers.map((h) => (
              <Table.Th key={h} scope="col">
                {h}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.line}>
              <Table.Td>{r.line}</Table.Td>
              <Table.Td>
                <CaseLink module={module} id={r.case_id} />
                {r.case_business_date !== fileDate && (
                  <Text size="xs" c="dimmed">
                    case of {formatDate(r.case_business_date)}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>{r.rrn}</Table.Td>
              {withRule && <Table.Td>{r.rule_code || '-'}</Table.Td>}
              <Table.Td>{replyCode(r.flag, r.code)}</Table.Td>
              <Table.Td>{replySourceLabel(r.reply_source)}</Table.Td>
              <Table.Td>{formatDate(r.shtdat)}</Table.Td>
              {/* As written in the file ("140.5"), not reformatted. */}
              <Table.Td ff="monospace">{r.amount}</Table.Td>
              <Table.Td ff="monospace">{r.account_masked || '-'}</Table.Td>
              <Table.Td className="wrap-anywhere">{r.text}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

// ---------------------------------------------------------------------------
// generated files
// ---------------------------------------------------------------------------
/** One file: what it held, what was left out then, and who generated and downloaded it. */
function NpciFileDrawer({
  module,
  file,
  onClose,
}: {
  module: string;
  file: NpciFileRow | null;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: ['npci-file', module, file?.id],
    queryFn: () => getNpciFile(module, file?.id as number),
    enabled: file !== null,
  });
  const f = query.data;
  // The id of the file whose rows are unfolded, so another file opens folded.
  const [rowsShownFor, setRowsShownFor] = useState<number | null>(null);
  const showRows = f !== undefined && rowsShownFor === f.id;
  const toggleRows = () => setRowsShownFor(showRows || !f ? null : f.id);
  const sources = f ? replySourcesSummary(countReplySources(f.rows)) : null;
  return (
    <Drawer opened={file !== null} onClose={onClose} position="right" size="xl" title={file?.file_name ?? ''}>
      {query.isPending && file !== null && <Skeleton h={200} />}
      {query.error && (
        <Alert color="red" role="alert" title="The file could not be loaded">
          {query.error.message}
        </Alert>
      )}
      {f && (
        <Stack gap="sm">
          <Text size="sm">
            {formatCount(f.row_count)} row(s), {formatRupees(f.total_amount)}. Generated by{' '}
            {f.generated_by || '-'} on {formatDateTime(f.generated_at)}.
            {f.partial ? ` Partial: ${formatCount(f.not_included_count)} case(s) were left out.` : ''}
          </Text>
          {sources && <Text size="sm">Replies: {sources}.</Text>}
          {f.remarks && <Text size="sm">Remarks: {f.remarks}</Text>}
          <Text size="xs" ff="monospace" className="wrap-anywhere">
            SHA-256 {f.sha256}
          </Text>
          {f.counts.length > 0 && (
            <CountsTable counts={f.counts} label={`Rows per reply in ${f.file_name}`} />
          )}
          {f.rows.length > 0 && (
            <Stack gap={4}>
              <Group>
                <Button size="xs" variant="subtle" onClick={toggleRows} aria-expanded={showRows}>
                  {showRows
                    ? 'Hide the rows'
                    : `Show the ${formatCount(f.rows.length)} row(s) of ${f.file_name}`}
                </Button>
              </Group>
              {showRows && (
                <FileRowsTable
                  module={module}
                  fileDate={f.file_date}
                  rows={f.rows}
                  label={`Rows of ${f.file_name}`}
                />
              )}
            </Stack>
          )}
          {f.warnings.length > 0 && (
            <Alert color="orange" title="Warnings when the file was generated">
              <List size="sm">
                {f.warnings.map((w) => (
                  <List.Item key={w}>{w}</List.Item>
                ))}
              </List>
            </Alert>
          )}
          {f.not_included.length > 0 && (
            <>
              <Title order={3} size="h5">
                Left out of {f.file_name} when it was generated
              </Title>
              <NotIncludedTable
                module={module}
                rows={f.not_included}
                label={`Cases left out of ${f.file_name}`}
              />
            </>
          )}
          <Title order={3} size="h5">
            History (generation and downloads)
          </Title>
          <OperationHistory entries={f.history} label={`History of ${f.file_name}`} />
        </Stack>
      )}
    </Drawer>
  );
}

/** Saves the bytes under the file's own name, unchanged. */
export function saveBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** "Downloaded N times", the last one, and the full list (owner 2026-10-23). */
export function DownloadHistory({ file }: { file: NpciFileRow }) {
  const downloads = file.downloads;
  if (!downloads) return <Text size="sm">-</Text>;
  const n = file.download_count ?? downloads.length;
  if (n === 0)
    return (
      <Text size="sm" c="dimmed">
        Not downloaded yet
      </Text>
    );
  const last = downloads[0];
  return (
    <Stack gap={2}>
      <Text size="sm">Downloaded {n === 1 ? 'once' : `${formatCount(n)} times`}</Text>
      {last && (
        <Text size="xs" c="dimmed">
          Last: {last.by || '-'}, {formatDateTime(last.at)}
        </Text>
      )}
      <Popover position="bottom-start" withArrow shadow="md">
        <Popover.Target>
          <Button size="compact-xs" variant="subtle" aria-label={`All downloads of ${file.file_name}`}>
            All downloads
          </Button>
        </Popover.Target>
        <Popover.Dropdown>
          <List size="sm" aria-label={`Downloads of ${file.file_name}`}>
            {downloads.map((d, i) => (
              <List.Item key={`${d.at}-${i}`}>
                {d.by || '-'}, {formatDateTime(d.at)}
              </List.Item>
            ))}
          </List>
          {downloads.length < n && (
            <Text size="xs" c="dimmed">
              The latest {downloads.length} of {formatCount(n)} shown.
            </Text>
          )}
        </Popover.Dropdown>
      </Popover>
    </Stack>
  );
}

function GeneratedFiles({ module, businessDate }: { module: string; businessDate: string }) {
  const qc = useQueryClient();
  const [viewing, setViewing] = useState<NpciFileRow | null>(null);
  const [downloading, setDownloading] = useState<number | null>(null);
  const query = useQuery({
    queryKey: ['npci-files', module, businessDate],
    queryFn: () => listNpciFiles(module, { file_date: businessDate, page_size: FILES_PAGE_SIZE }),
  });

  // Fetched as a Blob (the stored bytes, CRLF kept) so the list can show the new download once it is logged.
  const download = async (f: NpciFileRow) => {
    if (!f.download_url) return;
    setDownloading(f.id);
    try {
      saveBlob(await downloadNpciFile(f.download_url), f.file_name);
    } catch (e) {
      notifications.show({
        color: 'red',
        title: `${f.file_name} could not be downloaded`,
        message: e instanceof ApiError ? e.detail : 'The server could not be reached.',
      });
    } finally {
      setDownloading(null);
      void qc.invalidateQueries({ queryKey: ['npci-files', module] });
      void qc.invalidateQueries({ queryKey: ['npci-file', module, f.id] });
    }
  };
  const data = query.data;
  // Oldest first, as the sequence runs (_2, _3...); the list endpoint is newest first.
  const files = [...(data?.results ?? [])].sort((a, b) => a.sequence - b.sequence);
  // A file never downloaded keeps the date at "SSKUPI file to generate / download" (owner Q2).
  const notDownloaded = files.filter((f) => (f.download_count ?? f.downloads?.length) === 0);

  return (
    <Stack gap="xs" component="section" aria-labelledby="npci-files-title">
      <Title order={3} size="h5" id="npci-files-title">
        Files generated for {formatDate(businessDate)}
      </Title>
      {query.isPending && <Skeleton h={60} />}
      {query.error && (
        <Alert color="red" role="alert" title="The generated files could not be loaded">
          {query.error.message}
        </Alert>
      )}
      {data && files.length === 0 && (
        <Text size="sm" c="dimmed">
          No file generated for this date yet.
        </Text>
      )}
      {data && files.length > 0 && (
        <>
          {notDownloaded.length > 0 && (
            <Alert color="orange" title="Not downloaded yet">
              <Group gap="xs" wrap="wrap">
                <Text size="sm">
                  {notDownloaded.map((f) => f.file_name).join(', ')}{' '}
                  {notDownloaded.length === 1 ? 'has' : 'have'} not been downloaded. Download{' '}
                  {notDownloaded.length === 1 ? 'it' : 'them'} and upload to the NPCI portal.
                </Text>
                {notDownloaded
                  .filter((f) => f.download_url)
                  .map((f) => (
                    <Button
                      key={f.id}
                      size="xs"
                      onClick={() => void download(f)}
                      loading={downloading === f.id}
                    >
                      Download {f.file_name} now
                    </Button>
                  ))}
              </Group>
            </Alert>
          )}
          {!data.can_download && (
            <Text size="sm" c="dimmed">
              Your role sees the list only. A Maker, Checker or Operations User downloads the files.
            </Text>
          )}
          <Table.ScrollContainer minWidth={1250}>
            <Table withTableBorder aria-label={`SSKUPI files generated for ${formatDate(businessDate)}`}>
              <Table.Thead>
                <Table.Tr>
                  {['File', 'Rows', 'Amount', 'Generated by', 'Generated', 'SHA-256', 'Downloads', ''].map(
                    (h, i) => (
                      <Table.Th key={i} scope="col">
                        {h}
                      </Table.Th>
                    ),
                  )}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {files.map((f) => (
                  <Table.Tr key={f.id}>
                    <Table.Td>
                      <Text size="sm" fw={600}>
                        {f.file_name}
                      </Text>
                      {f.partial && (
                        <Badge size="sm" variant="light" color="orange">
                          Partial: {formatCount(f.not_included_count)} left out
                        </Badge>
                      )}
                      {f.remarks && (
                        <Text size="xs" c="dimmed" className="wrap-anywhere">
                          {f.remarks}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>{formatCount(f.row_count)}</Table.Td>
                    <Table.Td>{formatRupees(f.total_amount)}</Table.Td>
                    <Table.Td>{f.generated_by || '-'}</Table.Td>
                    <Table.Td>{formatDateTime(f.generated_at)}</Table.Td>
                    <Table.Td maw={220}>
                      <Text size="xs" ff="monospace" className="wrap-anywhere" title={f.sha256}>
                        {f.sha256}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <DownloadHistory file={f} />
                    </Table.Td>
                    <Table.Td>
                      <Group gap={4} wrap="nowrap">
                        {f.download_url && (
                          <Button
                            size="xs"
                            onClick={() => void download(f)}
                            loading={downloading === f.id}
                            aria-label={`Download ${f.file_name}`}
                          >
                            Download
                          </Button>
                        )}
                        <Button
                          size="xs"
                          variant="default"
                          onClick={() => setViewing(f)}
                          aria-label={`Details of ${f.file_name}`}
                        >
                          Details
                        </Button>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </>
      )}
      <NpciFileDrawer module={module} file={viewing} onClose={() => setViewing(null)} />
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// generate
// ---------------------------------------------------------------------------
function GenerateModal({
  module,
  preview,
  opened,
  onClose,
  onGenerated,
  onStale,
}: {
  module: string;
  preview: NpciFilePreview;
  opened: boolean;
  onClose: () => void;
  onGenerated: () => void;
  /** The server's answer differs from the preview: reload it. */
  onStale: () => void;
}) {
  const [remarks, setRemarks] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  // The server asked for the confirmation although the preview was complete (it changed meanwhile).
  const [serverAsked, setServerAsked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  // Amendment 2: the confirmation is asked only when something is held back.
  const partial = preview.partial || preview.not_included.length > 0 || serverAsked;
  const name = preview.next_file_name;
  const summary = notIncludedSummary(preview);
  const sources = replySourcesSummary(preview.reply_sources);

  const close = () => {
    setRemarks('');
    setConfirmed(false);
    setServerAsked(false);
    setRefusal(null);
    onClose();
  };

  const generate = async () => {
    setBusy(true);
    setRefusal(null);
    try {
      const f = await generateNpciFile(module, preview.file_date, {
        confirm_partial: partial && confirmed,
        remarks: remarks.trim(),
      });
      notifications.show({
        color: 'green',
        title: `${f.file_name} generated`,
        message: `${formatCount(f.row_count)} row(s) included${f.partial ? ' (partial file)' : ''}. Download it and upload it to the NPCI portal.`,
      });
      onGenerated();
      close();
    } catch (e) {
      if (needsPartialConfirmation(e)) {
        setServerAsked(true);
        setConfirmed(false);
        setRefusal(
          `${refusalText(e)} The list below has been reloaded: check what is left out, then confirm.`,
        );
      } else {
        setRefusal(refusalText(e));
      }
      onStale();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal opened={opened} onClose={close} title={`Generate ${name}?`} size="lg" centered>
      <Stack gap="sm">
        <Text size="sm">
          {formatCount(preview.row_count)} repl{preview.row_count === 1 ? 'y' : 'ies'} will be included in
          file {name}, total {formatRupees(preview.total_amount)}.
        </Text>
        {sources && <Text size="sm">Replies: {sources}.</Text>}
        <Text size="sm">{CURRENT_REPLY_TEXT}</Text>
        {preview.next_sequence > 1 && (
          <Text size="sm">
            This is file number {preview.next_sequence} for {formatDate(preview.file_date)}: it holds only
            cases not included in an earlier file.
          </Text>
        )}
        <Text size="sm">
          The app only saves the file. It does not send it anywhere: download it and upload it to the NPCI
          portal yourself.
        </Text>
        {preview.warnings.length > 0 && (
          <Alert color="orange" title="Warnings">
            <List size="sm">
              {preview.warnings.map((w) => (
                <List.Item key={w}>{w}</List.Item>
              ))}
            </List>
          </Alert>
        )}
        {partial && (
          <Alert color="red" title="Partial file">
            <Stack gap={6}>
              <Text size="sm">
                {formatCount(preview.not_included.length)} case(s) of this date are left out of {name}:
              </Text>
              {summary.length > 0 && (
                <List size="sm">
                  {summary.map((s) => (
                    <List.Item key={s}>{s}</List.Item>
                  ))}
                </List>
              )}
              <Text size="sm">
                Each is listed under "Not included" on this page with its reason. A case goes into a later
                file for the date once its reason is resolved: a Checker's approval, a new reply from the
                Maker, a cleared review or a filled-in numeric code.
              </Text>
              <Checkbox
                label={`I confirm that ${name} is a partial file`}
                checked={confirmed}
                onChange={(e) => setConfirmed(e.currentTarget.checked)}
              />
            </Stack>
          </Alert>
        )}
        <Textarea
          label="Remarks (optional)"
          autosize
          minRows={2}
          value={remarks}
          onChange={(e) => setRemarks(e.currentTarget.value)}
        />
        {refusal && (
          <Alert color="red" role="alert" title="The file was not generated">
            {refusal}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button variant="default" onClick={close} disabled={busy}>
            Cancel
          </Button>
          <Button
            onClick={generate}
            loading={busy}
            disabled={(partial && !confirmed) || preview.row_count === 0}
            data-autofocus
          >
            {partial ? 'Generate partial file' : 'Generate file'}
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// panel
// ---------------------------------------------------------------------------
function PreviewBody({
  module,
  businessDate,
  preview,
  canGenerate,
  onRefresh,
  filesList,
}: {
  module: string;
  businessDate: string;
  preview: NpciFilePreview;
  canGenerate: boolean;
  onRefresh: () => void;
  /** The generated files, shown right under the action. */
  filesList: ReactNode;
}) {
  const qc = useQueryClient();
  const [generating, setGenerating] = useState(false);
  const [showRows, setShowRows] = useState(false);
  const name = preview.next_file_name;
  const summary = notIncludedSummary(preview);
  const sources = replySourcesSummary(preview.reply_sources);
  const changed = preview.changed_since_included_count ?? 0;

  const generated = () => {
    void qc.invalidateQueries({ queryKey: ['npci-preview', module, businessDate] });
    void qc.invalidateQueries({ queryKey: ['npci-files', module] });
    void qc.invalidateQueries({ queryKey: ['date', module, businessDate] });
  };

  return (
    <Stack gap="sm">
      {/* The action first (owner 2026-10-27: the Generate button was hard to find). */}
      <Card
        withBorder
        padding="sm"
        bg="var(--mantine-color-blue-light)"
        role="group"
        aria-label="Next SSKUPI file"
      >
        {preview.row_count > 0 ? (
          <Stack gap="xs">
            <Text fw={600}>
              {formatCount(preview.row_count)} reply row(s) ready for {name} (
              {formatRupees(preview.total_amount)}).
            </Text>
            {canGenerate ? (
              <Group>
                <Button size="md" onClick={() => setGenerating(true)} disabled={!preview.can_generate}>
                  Generate {name}
                </Button>
              </Group>
            ) : (
              <Text size="sm">A Checker generates the file.</Text>
            )}
          </Stack>
        ) : (
          <Stack gap={4}>
            <Text fw={600}>{nothingNewLine(preview)}</Text>
            {!canGenerate && <Text size="sm">A Checker generates the file.</Text>}
          </Stack>
        )}
      </Card>

      {filesList}

      <Text size="sm" c="dimmed">
        The app generates the SSKUPI reply file only. Download it and upload it to the NPCI portal yourself.{' '}
        {CURRENT_REPLY_TEXT} A case is included in one file only.
      </Text>

      <Group gap="xs" wrap="wrap">
        <Text fw={600}>Next file: {name}</Text>
        <Text size="sm">
          {formatCount(preview.row_count)} row(s), {formatRupees(preview.total_amount)}
        </Text>
        {preview.partial && preview.row_count > 0 && (
          <Badge variant="light" color="orange">
            Partial
          </Badge>
        )}
      </Group>
      {sources && preview.row_count > 0 && <Text size="sm">Replies: {sources}.</Text>}

      {preview.already_sent_count > 0 && (
        <Text size="sm">
          {formatCount(preview.already_sent_count)} case(s) of this date are already included in{' '}
          {preview.already_sent_files.length === 1 ? 'file' : 'files'} {preview.already_sent_files.join(', ')}
          . They are not included again.
        </Text>
      )}

      {changed > 0 && (
        <div role="group" aria-label="Cases changed since included">
          <Alert color="orange" title="Changed since included">
            {formatCount(changed)} case(s) already included in a file of this date have changed since (flagged
            for review, another reply chosen, or closed by a re-run). The file already generated does not
            change and the case is not included again: any correction is made with NPCI outside the app.
          </Alert>
        </div>
      )}

      {preview.warnings.length > 0 && (
        <div role="group" aria-label="SSKUPI file warnings">
          <Alert color="orange" title="Warnings">
            <List size="sm">
              {preview.warnings.map((w) => (
                <List.Item key={w}>{w}</List.Item>
              ))}
            </List>
          </Alert>
        </div>
      )}

      {preview.counts.length > 0 && (
        <CountsTable counts={preview.counts} label={`Rows per reply in ${name}`} />
      )}

      {preview.rows.length > 0 && (
        <Stack gap={4}>
          <Group>
            <Button
              size="xs"
              variant="subtle"
              onClick={() => setShowRows((v) => !v)}
              aria-expanded={showRows}
            >
              {showRows ? 'Hide the rows' : `Show the ${formatCount(preview.rows.length)} row(s) of ${name}`}
            </Button>
          </Group>
          {showRows && (
            <>
              <Text size="xs" c="dimmed">
                Account numbers show their last 4 characters only; the full values are in the downloaded file.
              </Text>
              <FileRowsTable
                module={module}
                fileDate={preview.file_date}
                rows={preview.rows}
                label={`Rows of ${name}`}
              />
            </>
          )}
        </Stack>
      )}

      {preview.not_included.length > 0 && (
        <Stack gap={4} component="section" aria-labelledby="npci-not-included-title">
          <Title order={3} size="h5" id="npci-not-included-title">
            Not included in {name}: {formatCount(preview.not_included.length)} case(s)
          </Title>
          {summary.length > 0 && (
            <List size="sm">
              {summary.map((s) => (
                <List.Item key={s}>{s}</List.Item>
              ))}
            </List>
          )}
          <NotIncludedTable
            module={module}
            rows={preview.not_included}
            label={`Cases not included in ${name}`}
          />
        </Stack>
      )}

      {canGenerate && (
        <GenerateModal
          module={module}
          preview={preview}
          opened={generating}
          onClose={() => setGenerating(false)}
          onGenerated={generated}
          onStale={onRefresh}
        />
      )}
    </Stack>
  );
}

export function NpciFilePanel({
  module,
  businessDate,
  bare = false,
}: {
  module: string;
  businessDate: string;
  /** Inside the date page's "6. SSKUPI file" step: no card or heading of its own. */
  bare?: boolean;
}) {
  const me = useMe();
  const visible = can(me.permissions, 'npci_files.view');
  const query = useQuery({
    queryKey: ['npci-preview', module, businessDate],
    queryFn: () => getNpciFilePreview(module, businessDate),
    enabled: visible,
  });
  if (!visible) return null;
  const preview = query.data;
  const noFormat = query.error instanceof ApiError && query.error.status === 404;
  const filesList = <GeneratedFiles module={module} businessDate={businessDate} />;

  let body;
  if (noFormat) {
    body = <Text size="sm">{(query.error as ApiError).detail}</Text>;
  } else if (query.isPending) {
    body = <Skeleton h={120} />;
  } else if (query.error || !preview) {
    body = (
      <>
        <Alert color="red" role="alert" title="The SSKUPI file preview could not be loaded">
          {query.error?.message}
        </Alert>
        {filesList}
      </>
    );
  } else {
    body = (
      <PreviewBody
        module={module}
        businessDate={businessDate}
        preview={preview}
        canGenerate={can(me.permissions, 'npci_files.generate')}
        onRefresh={() => void query.refetch()}
        filesList={filesList}
      />
    );
  }

  if (bare) return <Stack gap="sm">{body}</Stack>;
  return (
    <Card withBorder padding="md" component="section" aria-labelledby="npci-file-title">
      <Stack gap="sm">
        <Title order={2} size="h4" id="npci-file-title">
          SSKUPI file
        </Title>
        {body}
      </Stack>
    </Card>
  );
}
