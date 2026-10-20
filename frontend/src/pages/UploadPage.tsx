/**
 * File upload with a review table (design 4.8, P5 c, Amendment 1 steps 1-2).
 *
 *   1. The user picks files (or a whole day's folder).
 *   2. "Check files": the SPA reads each file's start and SHA-256 and the
 *      server PROPOSES type, cycle and business date (POST files/detect/;
 *      nothing is saved). The upload's business date is pre-filled from the
 *      file names.
 *   3. The user confirms or corrects each row (send, type, cycle) and the
 *      business date, then uploads. Each file is accepted or refused on its
 *      own; the result table says why.
 * CBS covers several dates: its date is confirmed after processing, on the
 * file's own screen (4.10). Operations User only (P10b); the server decides.
 */
import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Group,
  List,
  NativeSelect,
  Progress,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { DateInput } from '@mantine/dates';
import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';

import { ApiError, detectFiles } from '../api/client';
import type { DetectResult, FileTypeOption } from '../api/types';
import { useMe } from '../auth/auth';
import { FileStateBadge } from '../components/Badges';
import { useModuleCode } from '../hooks/module';
import { formatBytes, formatDate } from '../lib/format';
import { can } from '../lib/roles';
import {
  buildRows,
  reviewTotals,
  rowNotes,
  rowProblems,
  sha256Hex,
  typeOf,
  uploadRows,
  withCycle,
  withType,
  type ReviewRow,
  type UploadOutcome,
} from '../lib/upload';

type Phase = 'pick' | 'checking' | 'review' | 'uploading' | 'done';

/** An ApiError's detail plus its field messages, for an alert. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    const fields = Object.values(e.fieldErrors).flat();
    return [e.detail, ...fields].join(' ');
  }
  return 'The server could not be reached.';
}

function ReviewTable({
  rows,
  types,
  businessDate,
  maxFileBytes,
  disabled,
  onChange,
}: {
  rows: ReviewRow[];
  types: FileTypeOption[];
  businessDate: string | null;
  maxFileBytes: number;
  disabled: boolean;
  onChange: (row: ReviewRow) => void;
}) {
  const typeData = [
    { value: '', label: 'Choose...' },
    ...types.map((t) => ({ value: String(t.id), label: t.label })),
  ];
  return (
    <Table.ScrollContainer minWidth={1100}>
      <Table aria-label="Files to upload" verticalSpacing={4}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Send</Table.Th>
            <Table.Th scope="col">File</Table.Th>
            <Table.Th scope="col">Proposed</Table.Th>
            <Table.Th scope="col">File type</Table.Th>
            <Table.Th scope="col">Cycle</Table.Th>
            <Table.Th scope="col">Date in name</Table.Th>
            <Table.Th scope="col">Warnings</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => {
            const name = row.file.name;
            const t = typeOf(types, row.typeId);
            const p = row.detected.proposal;
            const problems = rowProblems(row, types);
            const warnings = [
              ...(row.detected.duplicate ? [row.detected.duplicate.message] : []),
              // The server's warnings were for the proposed type; after a change the upload re-checks.
              ...(row.changed ? [] : row.detected.warnings),
              ...rowNotes(row, types, businessDate, maxFileBytes),
            ];
            return (
              <Table.Tr key={row.index}>
                <Table.Td>
                  <Checkbox
                    aria-label={`Send ${name}`}
                    checked={row.include}
                    disabled={disabled}
                    onChange={(e) => onChange({ ...row, include: e.currentTarget.checked })}
                  />
                </Table.Td>
                <Table.Td>
                  <Text size="sm" className="wrap-anywhere">
                    {name}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {formatBytes(row.file.size)}
                    {row.detected.detected_format ? `, ${row.detected.detected_format}` : ''}
                  </Text>
                </Table.Td>
                <Table.Td>
                  {p ? (
                    <>
                      <Text size="sm">
                        {p.label}
                        {p.cycle ? ` ${p.cycle}` : ''}
                      </Text>
                      <Text size="xs" c="dimmed">
                        {p.matched_by === 'content' ? 'from the content' : 'from the file name'}
                      </Text>
                    </>
                  ) : (
                    <Badge color="orange" variant="light">
                      Not recognised
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <NativeSelect
                    aria-label={`File type for ${name}`}
                    data={typeData}
                    value={row.typeId === null ? '' : String(row.typeId)}
                    onChange={(e) =>
                      onChange(withType(row, e.currentTarget.value ? Number(e.currentTarget.value) : null, types))
                    }
                    disabled={disabled || !row.include}
                    size="xs"
                    w={220}
                  />
                </Table.Td>
                <Table.Td>
                  {t?.is_cycle_wise ? (
                    <NativeSelect
                      aria-label={`Cycle for ${name}`}
                      data={[{ value: '', label: 'Choose...' }, ...t.cycles]}
                      value={row.cycle ?? ''}
                      onChange={(e) => onChange(withCycle(row, e.currentTarget.value))}
                      disabled={disabled || !row.include}
                      size="xs"
                      w={90}
                    />
                  ) : (
                    <Text size="sm" c="dimmed">
                      daily
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{p?.business_date ? formatDate(p.business_date) : '-'}</Table.Td>
                <Table.Td>
                  {problems.map((m) => (
                    <Text key={m} size="sm" c="red.8" fw={600}>
                      {m}
                    </Text>
                  ))}
                  {warnings.length > 0 && (
                    <List size="xs" spacing={2}>
                      {warnings.map((w) => (
                        <List.Item key={w} className="wrap-anywhere">
                          {w}
                        </List.Item>
                      ))}
                    </List>
                  )}
                  {problems.length === 0 && warnings.length === 0 && (
                    <Text size="sm" c="dimmed">
                      none
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function ResultsTable({ module, outcomes }: { module: string; outcomes: UploadOutcome[] }) {
  return (
    <Table.ScrollContainer minWidth={900}>
      <Table aria-label="Upload results">
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">File</Table.Th>
            <Table.Th scope="col">Result</Table.Th>
            <Table.Th scope="col">Details</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {outcomes.map((o) => (
            <Table.Tr key={o.index}>
              <Table.Td className="wrap-anywhere">
                {o.accepted ? (
                  <Anchor component={Link} to={`/${module}/files/${o.accepted.id}`}>
                    {o.filename}
                  </Anchor>
                ) : (
                  o.filename
                )}
              </Table.Td>
              <Table.Td>
                {o.accepted ? (
                  <Badge color="green" variant="light">
                    Accepted
                  </Badge>
                ) : (
                  <Badge color="red" variant="filled">
                    Refused
                  </Badge>
                )}
              </Table.Td>
              <Table.Td>
                {o.accepted ? (
                  <Group gap="xs">
                    <Text size="sm">
                      {o.accepted.file_type.label}
                      {o.accepted.cycle ? ` ${o.accepted.cycle}` : ''}, file #{o.accepted.id}
                    </Text>
                    <FileStateBadge state={o.accepted.state} />
                  </Group>
                ) : (
                  <List size="sm">
                    {o.errors.map((e) => (
                      <List.Item key={e} className="wrap-anywhere">
                        {e}
                      </List.Item>
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

function Working({ verb, progress }: { verb: string; progress: { done: number; total: number; current: string } }) {
  return (
    <Stack gap={4}>
      <Text size="sm" role="status">
        {progress.done < progress.total
          ? `${verb} ${progress.done + 1} of ${progress.total}: ${progress.current}`
          : 'Waiting for the server...'}
      </Text>
      <Progress value={progress.total ? (100 * progress.done) / progress.total : 0} aria-hidden />
    </Stack>
  );
}

export function UploadPage() {
  const me = useMe();
  const module = useModuleCode();
  const qc = useQueryClient();
  const [phase, setPhase] = useState<Phase>('pick');
  const [picked, setPicked] = useState<File[]>([]);
  const [detect, setDetect] = useState<DetectResult | null>(null);
  const [rows, setRows] = useState<ReviewRow[]>([]);
  const [businessDate, setBusinessDate] = useState<string | null>(null);
  const [progress, setProgress] = useState({ done: 0, total: 0, current: '' });
  const [outcomes, setOutcomes] = useState<UploadOutcome[]>([]);
  const [error, setError] = useState<string | null>(null);

  if (!can(me.permissions, 'files.upload')) {
    return (
      <Stack>
        <Title order={1} size="h3">
          Upload files
        </Title>
        <Alert color="gray" title="Not available to your role">
          Uploading files, choosing the CBS business date and starting a run are for the Operations User.{' '}
          <Anchor component={Link} to={`/${module}/files`}>
            See the files
          </Anchor>
        </Alert>
      </Stack>
    );
  }

  const startOver = () => {
    setPhase('pick');
    setPicked([]);
    setDetect(null);
    setRows([]);
    setBusinessDate(null);
    setOutcomes([]);
    setError(null);
  };

  const check = async () => {
    setError(null);
    setPhase('checking');
    try {
      const hashed: { file: File; sha256: string }[] = [];
      for (const [i, file] of picked.entries()) {
        setProgress({ done: i, total: picked.length, current: file.name });
        hashed.push({ file, sha256: await sha256Hex(file) });
      }
      setProgress({ done: picked.length, total: picked.length, current: '' });
      const result = await detectFiles(module, hashed);
      setDetect(result);
      setRows(buildRows(hashed, result));
      setBusinessDate(result.business_date);
      setPhase('review');
    } catch (e) {
      setError(errorText(e));
      setPhase('pick');
    }
  };

  const upload = async () => {
    if (!businessDate) return;
    setError(null);
    setPhase('uploading');
    try {
      const out = await uploadRows(module, businessDate, rows, (done, total, current) =>
        setProgress({ done, total, current }),
      );
      setOutcomes(out);
      setPhase('done');
    } catch (e) {
      setError(errorText(e));
      setPhase('review');
    } finally {
      void qc.invalidateQueries({ queryKey: ['files', module] });
      void qc.invalidateQueries({ queryKey: ['dates', module] });
      void qc.invalidateQueries({ queryKey: ['date', module] });
      void qc.invalidateQueries({ queryKey: ['summary', module] });
    }
  };

  const types = detect?.file_types ?? [];
  const totals = reviewTotals(rows, types);
  const accepted = outcomes.filter((o) => o.accepted);
  // CBS (RANGE_CONTAINS) has no business date until it is confirmed.
  const cbs = accepted.filter((o) => o.accepted?.business_date === null);
  const busy = phase === 'checking' || phase === 'uploading';

  return (
    <Stack>
      <Title order={1} size="h3">
        Upload files
      </Title>

      {error && (
        <Alert color="red" role="alert" title="Not done">
          {error}
        </Alert>
      )}

      {(phase === 'pick' || phase === 'checking') && (
        <Stack gap="xs" component="section" aria-label="Pick files">
          <Text size="sm">
            Choose the day&apos;s files, or the whole folder. The system proposes each file&apos;s type, cycle and
            business date; you check them before anything is saved.
          </Text>
          <Group gap="lg" align="flex-end">
            <div>
              <Text component="label" htmlFor="upload-files" size="sm" fw={500} display="block">
                Choose files
              </Text>
              <input
                id="upload-files"
                type="file"
                multiple
                disabled={busy}
                onChange={(e) => setPicked(Array.from(e.currentTarget.files ?? []))}
              />
            </div>
            <div>
              <Text component="label" htmlFor="upload-folder" size="sm" fw={500} display="block">
                Or choose a folder
              </Text>
              <input
                id="upload-folder"
                type="file"
                multiple
                disabled={busy}
                // Not in React's types; supported by Edge and Chrome (design 7.5).
                ref={(el) => el?.setAttribute('webkitdirectory', '')}
                onChange={(e) => setPicked(Array.from(e.currentTarget.files ?? []))}
              />
            </div>
          </Group>
          <Text size="sm" aria-live="polite">
            {picked.length === 0 ? 'No file chosen.' : `${picked.length} file(s) chosen.`}
          </Text>
          {phase === 'checking' && <Working verb="Reading" progress={progress} />}
          <Group>
            <Button onClick={check} disabled={picked.length === 0} loading={phase === 'checking'}>
              Check files
            </Button>
          </Group>
        </Stack>
      )}

      {(phase === 'review' || phase === 'uploading') && detect && (
        <Stack gap="sm" component="section" aria-label="Review">
          <Group align="flex-end" gap="lg" wrap="wrap">
            <DateInput
              label="Business date of this upload"
              description={
                detect.business_date
                  ? `Pre-filled from the file names (${formatDate(detect.business_date)}).`
                  : 'No date found in the file names; choose it.'
              }
              valueFormat="DD-MMM-YYYY"
              value={businessDate}
              onChange={setBusinessDate}
              required
              disabled={busy}
              w={260}
            />
            <Text size="sm">
              {rows.length} file(s): {totals.included} to send
              {totals.duplicates ? `, ${totals.duplicates} uploaded before (unticked)` : ''}
              {totals.unrecognised ? `, ${totals.unrecognised} not recognised` : ''}.
            </Text>
          </Group>
          {detect.warnings.length > 0 && (
            <Alert color="orange" title="Check before uploading">
              <List size="sm">
                {detect.warnings.map((w) => (
                  <List.Item key={w}>{w}</List.Item>
                ))}
              </List>
            </Alert>
          )}
          <ReviewTable
            rows={rows}
            types={types}
            businessDate={businessDate}
            maxFileBytes={detect.max_file_bytes}
            disabled={busy}
            onChange={(row) => setRows((prev) => prev.map((r) => (r.index === row.index ? row : r)))}
          />
          {phase === 'uploading' && <Working verb="Uploading" progress={progress} />}
          {totals.blocked > 0 && (
            <Text size="sm" c="red.8" role="status">
              {totals.blocked} file(s) need a type or cycle before uploading (or untick them).
            </Text>
          )}
          <Group>
            <Button
              onClick={upload}
              loading={phase === 'uploading'}
              disabled={!businessDate || totals.included === 0 || totals.blocked > 0}
            >
              Upload {totals.included} file(s)
            </Button>
            <Button variant="default" onClick={startOver} disabled={busy}>
              Start over
            </Button>
          </Group>
        </Stack>
      )}

      {phase === 'done' && businessDate && (
        <Stack gap="sm" component="section" aria-label="Results">
          <Text fw={600} role="status">
            {accepted.length} accepted, {outcomes.length - accepted.length} refused (business date{' '}
            {formatDate(businessDate)}).
          </Text>
          {accepted.length > 0 && (
            <Text size="sm">
              Accepted files are now being processed. Follow them on the{' '}
              <Anchor component={Link} to={`/${module}/dates/${businessDate}`}>
                business date {formatDate(businessDate)}
              </Anchor>{' '}
              or in the{' '}
              <Anchor component={Link} to={`/${module}/files?business_date=${businessDate}`}>
                files list
              </Anchor>
              .
            </Text>
          )}
          {cbs.length > 0 && (
            <Alert color="violet" title="CBS business date">
              Once processed, {cbs.map((o) => o.filename).join(', ')} will wait for its business date: open the
              file and confirm the date (pre-selected: {formatDate(businessDate)}, when it is in the file).
            </Alert>
          )}
          <ResultsTable module={module} outcomes={outcomes} />
          <Group>
            <Button variant="default" onClick={startOver}>
              Upload more files
            </Button>
          </Group>
        </Stack>
      )}
    </Stack>
  );
}
