/**
 * "Rev credit file" on the business-date page (plan B section 3, owner
 * 2026-11-11 / 2026-11-12): the CBS credit file ("upi rev 02112026.csv") for
 * the cases that need a customer credit (R07r, R02 DRC 103, R04b TCC 103 once
 * decided). It comes before the SSKUPI step: once the checked file is
 * uploaded, the approved DRC 103 / TCC 103 replies go into the SSKUPI file.
 *
 * The cycle, in the screen's words:
 *   1. Generate: the server saves the next version of the date's file (an
 *      earlier version not yet uploaded is superseded).
 *   2. Download it, open it in Excel, fill in or correct the accounts the
 *      Check column asks for, delete rows not to credit now, save as CSV.
 *   3. Upload the checked file: all or nothing. Every case it still lists is
 *      approved and closed; deleted rows leave their cases open for a later
 *      version. A refusal lists the problems per row and approves nothing.
 *
 * Owner 2026-11-12: anyone signed in may generate, download and upload
 * (including the person who generated it). The buttons still follow the
 * server's flags (can_generate, download_url, can_upload); the server checks
 * every request. Account numbers show their last 4 characters only.
 */
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Input,
  List,
  Modal,
  Skeleton,
  Stack,
  Table,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link } from 'react-router';

import {
  ApiError,
  downloadBlob,
  generateCreditVoucher,
  getCreditVoucher,
  getCreditVoucherPreview,
  listCreditVouchers,
  uploadCreditVoucher,
} from '../../api/client';
import type {
  CreditVoucherLine,
  CreditVoucherList,
  CreditVoucherNotIncluded,
  CreditVoucherPreview,
  CreditVoucherRow,
  CreditVoucherRowError,
  CreditVoucherUploadResult,
} from '../../api/types';
import type { StepState } from '../../components/DayStatus';
import { DetailsSection } from '../../components/DetailsSection';
import { formatCount, formatDate, formatDateTime, formatRupees } from '../../lib/format';
import { saveBlob } from './NpciFilePanel';

/** The query keys (the date page reads the same two for its step badge and the SSKUPI hint). */
export const creditVoucherKeys = {
  list: (module: string, businessDate: string) => ['credit-voucher', module, businessDate] as const,
  preview: (module: string, businessDate: string) =>
    ['credit-voucher-preview', module, businessDate] as const,
};

export function useCreditVoucherList(module: string, businessDate: string, enabled = true) {
  return useQuery({
    queryKey: creditVoucherKeys.list(module, businessDate),
    queryFn: () => listCreditVouchers(module, businessDate),
    enabled,
  });
}

export function useCreditVoucherPreview(module: string, businessDate: string, enabled = true) {
  return useQuery({
    queryKey: creditVoucherKeys.preview(module, businessDate),
    queryFn: () => getCreditVoucherPreview(module, businessDate),
    enabled,
  });
}

/** The short helper text: download, check, upload. */
export const REV_HELP_TEXT =
  'Download the file, check it in Excel (fill in the missing accounts the Check column asks for, delete any row ' +
  'not to credit now), save it as CSV and upload it here. Uploading the checked file approves and closes the ' +
  'cases it lists; deleted rows leave their cases open for a later version.';

const STATUS_BADGE: Readonly<Record<string, { color: string; text: string }>> = {
  GENERATED: { color: 'orange', text: 'Generated' },
  APPROVED: { color: 'green', text: 'Approved' },
  SUPERSEDED: { color: 'gray', text: 'Superseded' },
};

export function CreditVoucherStatusBadge({ v }: { v: Pick<CreditVoucherRow, 'status' | 'status_label'> }) {
  const s = STATUS_BADGE[v.status];
  return (
    <Badge color={s?.color ?? 'gray'} variant="light" title={v.status_label}>
      {s?.text ?? (v.status_label || v.status)}
    </Badge>
  );
}

/**
 * The date page's step tick for the rev file: waiting for the checked file,
 * a file to generate, approved, or nothing to credit.
 */
export function revStep(
  list: CreditVoucherList | undefined,
  preview: Pick<CreditVoucherPreview, 'row_count'> | undefined,
  reconciled: boolean,
): { state: StepState; badge?: string } {
  if (!list) return { state: 'waiting' };
  if (list.pending_approval) return { state: 'attention', badge: 'Upload checked file' };
  if (preview && preview.row_count > 0) return { state: 'attention', badge: 'To generate' };
  if (list.latest?.status === 'APPROVED') return { state: 'done', badge: 'Approved' };
  if (!reconciled) return { state: 'waiting' };
  if (preview) return { state: 'done', badge: 'Nothing to credit' };
  return { state: 'waiting' };
}

/** "3 row(s) need an account", or null for none. */
export function needsAccountLine(n: number): string | null {
  return n > 0
    ? `${formatCount(n)} row(s) need an account number filled in or corrected (Check column).`
    : null;
}

/** The 400 refusal's per-row errors, read from the body (errors.rows holds objects, not strings). */
export function uploadRowErrors(e: unknown): CreditVoucherRowError[] {
  if (!(e instanceof ApiError)) return [];
  const rows = (e.body as { errors?: { rows?: unknown } } | null)?.errors?.rows;
  return Array.isArray(rows)
    ? (rows as CreditVoucherRowError[]).filter((r) => r && typeof r === 'object')
    : [];
}

/** The refusal's plain messages: errors.file and any other list of strings. */
export function uploadFileErrors(e: unknown): string[] {
  if (!(e instanceof ApiError)) return [];
  return Object.entries(e.fieldErrors)
    .filter(([k]) => k !== 'rows' && k !== 'cases')
    .flatMap(([, v]) => (Array.isArray(v) ? v.filter((m): m is string => typeof m === 'string') : []));
}

/** 409 "a case changed since generated": errors.cases. */
function changedCases(e: unknown): CreditVoucherNotIncluded[] {
  if (!(e instanceof ApiError)) return [];
  const cases = (e.body as { errors?: { cases?: unknown } } | null)?.errors?.cases;
  return Array.isArray(cases) ? (cases as CreditVoucherNotIncluded[]) : [];
}

function CaseLink({ module, id }: { module: string; id: number }) {
  return (
    <Anchor component={Link} to={`/${module}/cases/${id}`}>
      #{id}
    </Anchor>
  );
}

// ---------------------------------------------------------------------------
// tables
// ---------------------------------------------------------------------------
function CreditRowsTable({
  module,
  rows,
  label,
}: {
  module: string;
  rows: (CreditVoucherLine & { approved?: boolean })[];
  label: string;
}) {
  const withApproved = rows.some((r) => r.approved !== undefined);
  const headers = ['Sr. No.', 'Case', 'RRN', 'Rule', 'Txn date', 'Amount', 'Account', 'Check'];
  return (
    <Table.ScrollContainer minWidth={1000}>
      <Table withTableBorder aria-label={label}>
        <Table.Thead>
          <Table.Tr>
            {[...headers, ...(withApproved ? ['Approved'] : [])].map((h) => (
              <Table.Th key={h} scope="col">
                {h}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.line} bg={r.check ? 'var(--mantine-color-orange-light)' : undefined}>
              <Table.Td>{r.sr_no}</Table.Td>
              <Table.Td>
                <CaseLink module={module} id={r.case_id} />
              </Table.Td>
              <Table.Td>{r.rrn}</Table.Td>
              <Table.Td>{r.rule_code || '-'}</Table.Td>
              <Table.Td>{formatDate(r.txn_date)}</Table.Td>
              <Table.Td ff="monospace">{r.amount}</Table.Td>
              <Table.Td ff="monospace">{r.account_masked || '-'}</Table.Td>
              <Table.Td className="wrap-anywhere">
                {r.check ? (
                  <>
                    <Badge size="sm" color="orange" variant="filled">
                      Needs account
                    </Badge>
                    <Text size="xs">{r.check}</Text>
                  </>
                ) : (
                  '-'
                )}
              </Table.Td>
              {withApproved && <Table.Td>{r.approved ? 'Yes' : 'No'}</Table.Td>}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function NotIncludedTable({
  module,
  rows,
  label,
}: {
  module: string;
  rows: CreditVoucherNotIncluded[];
  label: string;
}) {
  return (
    <Table.ScrollContainer minWidth={800}>
      <Table withTableBorder aria-label={label}>
        <Table.Thead>
          <Table.Tr>
            {['Case', 'RRN', 'Rule', 'Case status', 'Why it is left out'].map((h) => (
              <Table.Th key={h} scope="col">
                {h}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((n) => (
            <Table.Tr key={`${n.case_id}-${n.rrn}`}>
              <Table.Td>
                <CaseLink module={module} id={n.case_id} />
              </Table.Td>
              <Table.Td>{n.rrn}</Table.Td>
              <Table.Td>{n.rule_code || '-'}</Table.Td>
              <Table.Td>{n.status || '-'}</Table.Td>
              <Table.Td className="wrap-anywhere">
                {n.reason_label || n.reason}
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

function RowErrorsTable({ rows }: { rows: CreditVoucherRowError[] }) {
  return (
    <Table.ScrollContainer minWidth={700}>
      <Table withTableBorder aria-label="Problems per row of the uploaded file">
        <Table.Thead>
          <Table.Tr>
            {['Line', 'Sr. No.', 'RRN', 'Problem'].map((h) => (
              <Table.Th key={h} scope="col">
                {h}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r, i) => (
            <Table.Tr key={`${r.line}-${i}`}>
              <Table.Td>{r.line}</Table.Td>
              <Table.Td>{r.sr_no || '-'}</Table.Td>
              <Table.Td>{r.rrn ?? '-'}</Table.Td>
              <Table.Td className="wrap-anywhere">
                {r.errors.length === 1 ? (
                  r.errors[0]
                ) : (
                  <List size="sm">
                    {r.errors.map((m) => (
                      <List.Item key={m}>{m}</List.Item>
                    ))}
                  </List>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

// ---------------------------------------------------------------------------
// generate
// ---------------------------------------------------------------------------
function GenerateModal({
  module,
  businessDate,
  preview,
  supersedes,
  opened,
  onClose,
  onDone,
}: {
  module: string;
  businessDate: string;
  preview: CreditVoucherPreview;
  /** The version not yet uploaded that a new one replaces. */
  supersedes: string | null;
  opened: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [remarks, setRemarks] = useState('');
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<string | null>(null);
  const name = preview.next_file_name;
  const close = () => {
    setRemarks('');
    setRefusal(null);
    onClose();
  };
  const generate = async () => {
    setBusy(true);
    setRefusal(null);
    try {
      const v = await generateCreditVoucher(module, businessDate, { remarks: remarks.trim() });
      notifications.show({
        color: 'green',
        title: `${v.file_name} generated`,
        message: `${formatCount(v.row_count)} credit row(s), ${formatRupees(v.total_amount)}. Download it, check it and upload the checked file.`,
      });
      close();
    } catch (e) {
      setRefusal(
        e instanceof ApiError
          ? [e.detail, ...uploadFileErrors(e)].join(' ')
          : 'The server could not be reached.',
      );
    } finally {
      setBusy(false);
      onDone();
    }
  };
  const check = needsAccountLine(preview.rows_to_check);
  return (
    <Modal opened={opened} onClose={close} title={`Generate ${name}?`} size="lg" centered>
      <Stack gap="sm">
        <Text size="sm">
          {formatCount(preview.row_count)} credit row(s) will be in {name}, total{' '}
          {formatRupees(preview.total_amount)}.
        </Text>
        {check && <Text size="sm">{check}</Text>}
        {supersedes && (
          <Alert color="orange" title="Replaces the file waiting for upload">
            {supersedes} has not been uploaded yet. Generating {name} supersedes it: its checked file can no
            longer be uploaded.
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
          <Button onClick={generate} loading={busy} data-autofocus>
            Generate file
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// upload
// ---------------------------------------------------------------------------
type UploadOutcome =
  | { kind: 'ok'; result: CreditVoucherUploadResult }
  | {
      kind: 'refused';
      detail: string;
      fileErrors: string[];
      rowErrors: CreditVoucherRowError[];
      cases: CreditVoucherNotIncluded[];
    };

export function UploadResultLine({
  r,
}: {
  r: Pick<CreditVoucherUploadResult, 'approved_count' | 'left_open_count'>;
}) {
  return (
    <Text fw={600}>
      {formatCount(r.approved_count)} approved, {formatCount(r.left_open_count)} left open
    </Text>
  );
}

function UploadOutcomeView({ module, outcome }: { module: string; outcome: UploadOutcome }) {
  if (outcome.kind === 'ok') {
    const r = outcome.result;
    return (
      <Alert color="green" title={`Checked file uploaded: ${r.voucher.file_name} approved`} role="status">
        <Stack gap={4}>
          <UploadResultLine r={r} />
          {r.accounts_changed > 0 && (
            <Text size="sm">
              {formatCount(r.accounts_changed)} account number(s) were filled in or corrected.
            </Text>
          )}
          {r.left_open.length > 0 && (
            <Text size="sm">
              Left open (deleted from the file):{' '}
              {r.left_open.map((c, i) => (
                <span key={c.case_id}>
                  {i > 0 && ', '}
                  <CaseLink module={module} id={c.case_id} /> ({c.rrn})
                </span>
              ))}
              . They can go into a later version.
            </Text>
          )}
          <Text size="sm">
            Next: run the reconciliation again for this date, then generate the SSKUPI file.
          </Text>
        </Stack>
      </Alert>
    );
  }
  return (
    <Alert color="red" role="alert" title="The checked file was refused; nothing was approved">
      <Stack gap="xs">
        <Text size="sm">{outcome.detail}</Text>
        {outcome.fileErrors.length > 0 && (
          <List size="sm">
            {outcome.fileErrors.map((m) => (
              <List.Item key={m}>{m}</List.Item>
            ))}
          </List>
        )}
        {outcome.rowErrors.length > 0 && <RowErrorsTable rows={outcome.rowErrors} />}
        {outcome.cases.length > 0 && (
          <NotIncludedTable
            module={module}
            rows={outcome.cases}
            label="Cases changed since the file was generated"
          />
        )}
      </Stack>
    </Alert>
  );
}

function UploadForm({
  module,
  businessDate,
  voucher,
  onUploaded,
}: {
  module: string;
  businessDate: string;
  voucher: CreditVoucherRow;
  onUploaded: (o: UploadOutcome) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [remarks, setRemarks] = useState('');
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const upload = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const result = await uploadCreditVoucher(module, businessDate, voucher.id, file, remarks.trim());
      onUploaded({ kind: 'ok', result });
      setFile(null);
      setRemarks('');
      if (input.current) input.current.value = '';
    } catch (e) {
      onUploaded({
        kind: 'refused',
        detail: e instanceof ApiError ? e.detail : 'The server could not be reached.',
        fileErrors: uploadFileErrors(e),
        rowErrors: uploadRowErrors(e),
        cases: changedCases(e),
      });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Stack
      gap="xs"
      component="form"
      aria-label={`Upload the checked ${voucher.file_name}`}
      onSubmit={(e) => {
        e.preventDefault();
        void upload();
      }}
    >
      <Input.Wrapper label="Checked file (CSV)" labelProps={{ htmlFor: 'rev-upload-file' }}>
        <input
          id="rev-upload-file"
          ref={input}
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => setFile(e.currentTarget.files?.[0] ?? null)}
          style={{ display: 'block' }}
        />
      </Input.Wrapper>
      <Textarea
        label="Remarks (optional)"
        autosize
        minRows={1}
        value={remarks}
        onChange={(e) => setRemarks(e.currentTarget.value)}
        maw={500}
      />
      <Group>
        <Button type="submit" disabled={!file} loading={busy}>
          Upload checked file
        </Button>
      </Group>
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// panel
// ---------------------------------------------------------------------------
function VersionsTable({
  versions,
  onDownload,
  downloading,
}: {
  versions: CreditVoucherRow[];
  onDownload: (v: CreditVoucherRow) => void;
  downloading: number | null;
}) {
  return (
    <Table.ScrollContainer minWidth={1000}>
      <Table withTableBorder aria-label="Versions of the rev credit file">
        <Table.Thead>
          <Table.Tr>
            {['File', 'Status', 'Rows', 'Amount', 'Generated', 'Approved', 'Downloads', ''].map((h, i) => (
              <Table.Th key={i} scope="col">
                {h}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {versions.map((v) => (
            <Table.Tr key={v.id}>
              <Table.Td>{v.file_name}</Table.Td>
              <Table.Td>
                <CreditVoucherStatusBadge v={v} />
              </Table.Td>
              <Table.Td>{formatCount(v.row_count)}</Table.Td>
              <Table.Td>{formatRupees(v.total_amount)}</Table.Td>
              <Table.Td>
                {v.generated_by || '-'}, {formatDateTime(v.generated_at)}
              </Table.Td>
              <Table.Td>
                {v.approval
                  ? `${v.approval.approved_by || '-'}, ${formatDateTime(v.approval.approved_at)}`
                  : '-'}
              </Table.Td>
              <Table.Td>{formatCount(v.download_count)}</Table.Td>
              <Table.Td>
                {v.download_url && (
                  <Button
                    size="xs"
                    variant="default"
                    onClick={() => onDownload(v)}
                    loading={downloading === v.id}
                    aria-label={`Download ${v.file_name}`}
                  >
                    Download
                  </Button>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

export function CreditVoucherPanel({ module, businessDate }: { module: string; businessDate: string }) {
  const qc = useQueryClient();
  const list = useCreditVoucherList(module, businessDate);
  const preview = useCreditVoucherPreview(module, businessDate);
  const latest = list.data?.latest ?? null;
  const pending = latest?.status === 'GENERATED';
  // The rows shown: the version waiting for its checked file, else what the next one would hold.
  const detail = useQuery({
    queryKey: ['credit-voucher-detail', module, businessDate, latest?.id],
    queryFn: () => getCreditVoucher(module, businessDate, latest?.id as number),
    enabled: pending && latest !== null,
  });
  const [generating, setGenerating] = useState(false);
  const [downloading, setDownloading] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<UploadOutcome | null>(null);
  const [showRows, setShowRows] = useState<boolean | null>(null);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['credit-voucher', module, businessDate] });
    void qc.invalidateQueries({ queryKey: ['credit-voucher-preview', module, businessDate] });
    void qc.invalidateQueries({ queryKey: ['credit-voucher-detail', module, businessDate] });
  };
  const uploaded = (o: UploadOutcome) => {
    setOutcome(o);
    refresh();
    if (o.kind === 'ok') {
      // The upload approved and closed cases: everything that counts them is stale.
      void qc.invalidateQueries({ queryKey: ['date', module, businessDate] });
      void qc.invalidateQueries({ queryKey: ['dates', module] });
      void qc.invalidateQueries({ queryKey: ['summary', module] });
      void qc.invalidateQueries({ queryKey: ['cases', module] });
      void qc.invalidateQueries({ queryKey: ['npci-preview', module, businessDate] });
    }
  };
  const download = async (v: CreditVoucherRow) => {
    if (!v.download_url) return;
    setDownloading(v.id);
    try {
      saveBlob(await downloadBlob(v.download_url), v.file_name);
    } catch (e) {
      notifications.show({
        color: 'red',
        title: `${v.file_name} could not be downloaded`,
        message: e instanceof ApiError ? e.detail : 'The server could not be reached.',
      });
    } finally {
      setDownloading(null);
      refresh();
    }
  };

  if (list.isPending || preview.isPending) return <Skeleton h={120} />;
  const error = list.error ?? preview.error;
  if (error || !list.data || !preview.data) {
    return (
      <Alert color="red" role="alert" title="The rev credit file could not be loaded">
        {error?.message}
      </Alert>
    );
  }
  const l = list.data;
  const p = preview.data;
  const canGenerate = l.can_generate && p.can_generate && p.row_count > 0;
  const rows: CreditVoucherLine[] = pending ? (detail.data?.rows ?? []) : p.rows;
  const rowsName = pending && latest ? latest.file_name : p.next_file_name;
  const rowsToCheck = pending && latest ? latest.rows_to_check : p.rows_to_check;
  const rowsOpen = showRows ?? rowsToCheck > 0;
  const check = needsAccountLine(rowsToCheck);

  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed">
        {REV_HELP_TEXT}
      </Text>

      {/* The action first, as in the SSKUPI step. */}
      <Card
        withBorder
        padding="sm"
        bg="var(--mantine-color-blue-light)"
        role="group"
        aria-label="Rev credit file"
      >
        <Stack gap="xs">
          {latest ? (
            <Group gap="xs" wrap="wrap">
              <Text fw={600}>{latest.file_name}</Text>
              <CreditVoucherStatusBadge v={latest} />
              <Text size="sm">
                {formatCount(latest.row_count)} row(s), {formatRupees(latest.total_amount)}
              </Text>
            </Group>
          ) : (
            <Text fw={600}>No rev credit file generated for this date yet.</Text>
          )}
          {latest && (
            <Text size="sm">
              Generated by {latest.generated_by || '-'} on {formatDateTime(latest.generated_at)}.{' '}
              {latest.download_count === 0
                ? 'Not downloaded yet.'
                : `Downloaded ${latest.download_count === 1 ? 'once' : `${formatCount(latest.download_count)} times`}.`}
            </Text>
          )}
          {latest?.approval && (
            <Text size="sm">
              Approved by {latest.approval.approved_by || '-'} on{' '}
              {formatDateTime(latest.approval.approved_at)} (uploaded {latest.approval.uploaded_file_name}):{' '}
              {formatCount(latest.approval.approved_count)} approved,{' '}
              {formatCount(latest.approval.left_open_count)} left open.
            </Text>
          )}
          {pending && check && (
            <Text size="sm" c="orange.8" fw={600}>
              {check}
            </Text>
          )}
          {/* While a version waits for its checked file, the preview lists the same cases again. */}
          {!pending &&
            (p.row_count > 0 ? (
              <Text size="sm">
                {formatCount(p.row_count)} credit row(s) ready for {p.next_file_name} (
                {formatRupees(p.total_amount)}).
                {p.rows_to_check > 0 && ` ${formatCount(p.rows_to_check)} of them need an account.`}
              </Text>
            ) : (
              <Text size="sm">No case of this date is waiting for a CBS credit.</Text>
            ))}
          <Group gap="xs" wrap="wrap">
            {canGenerate && (
              <Button onClick={() => setGenerating(true)} variant={pending ? 'default' : 'filled'}>
                {pending ? `Generate a new version (${p.next_file_name})` : `Generate ${p.next_file_name}`}
              </Button>
            )}
            {pending && latest?.download_url && (
              <Button
                onClick={() => void download(latest)}
                loading={downloading === latest.id}
                aria-label={`Download ${latest.file_name}`}
              >
                Download {latest.file_name}
              </Button>
            )}
          </Group>
          {pending && latest && latest.can_upload && (
            <UploadForm module={module} businessDate={businessDate} voucher={latest} onUploaded={uploaded} />
          )}
          {pending && latest && !latest.can_upload && (
            <Text size="sm">You may not upload the checked file for {latest.file_name}.</Text>
          )}
        </Stack>
      </Card>

      {outcome && <UploadOutcomeView module={module} outcome={outcome} />}

      {p.warnings.length > 0 && p.row_count > 0 && !pending && (
        <Alert color="orange" title="Warnings">
          <List size="sm">
            {p.warnings.map((w) => (
              <List.Item key={w}>{w}</List.Item>
            ))}
          </List>
        </Alert>
      )}

      {rows.length > 0 && (
        <Stack gap={4}>
          <Group>
            <Button
              size="xs"
              variant="subtle"
              onClick={() => setShowRows(!rowsOpen)}
              aria-expanded={rowsOpen}
            >
              {rowsOpen ? 'Hide the rows' : `Show the ${formatCount(rows.length)} row(s) of ${rowsName}`}
            </Button>
          </Group>
          {rowsOpen && (
            <>
              <Text size="xs" c="dimmed">
                Account numbers show their last 4 characters only; the full values are in the downloaded file.
                Highlighted rows need an account.
              </Text>
              <CreditRowsTable module={module} rows={rows} label={`Rows of ${rowsName}`} />
            </>
          )}
        </Stack>
      )}

      {p.not_included.length > 0 && (
        <Stack gap={4} component="section" aria-labelledby="rev-not-included-title">
          <Title order={3} size="h5" id="rev-not-included-title">
            Not in the next file: {formatCount(p.not_included.length)} case(s)
          </Title>
          <NotIncludedTable module={module} rows={p.not_included} label="Cases not in the rev credit file" />
        </Stack>
      )}

      {l.results.length > 0 && (
        <DetailsSection title={`All versions (${l.results.length})`} id="rev-versions-title" order={3}>
          <VersionsTable
            versions={l.results}
            onDownload={(v) => void download(v)}
            downloading={downloading}
          />
        </DetailsSection>
      )}

      {canGenerate && (
        <GenerateModal
          module={module}
          businessDate={businessDate}
          preview={p}
          supersedes={pending && latest ? latest.file_name : null}
          opened={generating}
          onClose={() => setGenerating(false)}
          onDone={() => {
            setOutcome(null);
            refresh();
            void qc.invalidateQueries({ queryKey: ['date', module, businessDate] });
          }}
        />
      )}
    </Stack>
  );
}
