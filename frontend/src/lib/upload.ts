/**
 * Upload review table logic (design 4.8, P5 c, Amendment 1), kept out of the
 * screen so it can be tested: the rows built from the detection dry run, the
 * user's corrections, what blocks the upload, and the per-file upload loop.
 *
 * The server decides every refusal (intake.upload_files); the checks here
 * only stop an upload that is certain to be refused (no type, no cycle) and
 * repeat the server's warnings in the row.
 */
import { ApiError, uploadFiles } from '../api/client';
import type { DetectFile, DetectResult, FileTypeOption, UploadedFileRow, UploadResult } from '../api/types';
import { formatDate } from './format';

export interface ReviewRow {
  /** Position in the user's list (= detect's index). */
  index: number;
  file: File;
  sha256: string;
  detected: DetectFile;
  /** Sent with the upload. Duplicates start unticked (the server would refuse them). */
  include: boolean;
  typeId: number | null;
  cycle: string | null;
  /** The user changed the proposed type or cycle (the server's warnings may no longer apply). */
  changed: boolean;
}

export function buildRows(files: { file: File; sha256: string }[], result: DetectResult): ReviewRow[] {
  return files.map(({ file, sha256 }, index) => {
    const detected = result.files.find((f) => f.index === index) ?? {
      index,
      filename: file.name,
      detected_format: '',
      proposal: null,
      duplicate: null,
      warnings: ['The server did not return a result for this file.'],
    };
    return {
      index,
      file,
      sha256,
      detected,
      include: !detected.duplicate,
      typeId: detected.proposal?.file_type_config_id ?? null,
      cycle: detected.proposal?.cycle ?? null,
      changed: false,
    };
  });
}

export const typeOf = (types: readonly FileTypeOption[], id: number | null) =>
  id === null ? undefined : types.find((t) => t.id === id);

/** A new file type for the row: keeps the cycle only when the new type has it. */
export function withType(row: ReviewRow, typeId: number | null, types: readonly FileTypeOption[]): ReviewRow {
  const t = typeOf(types, typeId);
  let cycle: string | null = null;
  if (t?.is_cycle_wise) {
    const proposed = row.detected.proposal?.cycle ?? null;
    if (row.cycle && t.cycles.includes(row.cycle)) cycle = row.cycle;
    else if (proposed && t.cycles.includes(proposed)) cycle = proposed;
  }
  return { ...row, typeId, cycle, changed: true };
}

export function withCycle(row: ReviewRow, cycle: string | null): ReviewRow {
  return { ...row, cycle: cycle || null, changed: true };
}

/** What blocks the upload of an included row (the server would refuse it for certain). */
export function rowProblems(row: ReviewRow, types: readonly FileTypeOption[]): string[] {
  if (!row.include) return [];
  const t = typeOf(types, row.typeId);
  if (!t) return ['Choose the file type.'];
  if (t.is_cycle_wise && !row.cycle) return [`Choose the cycle (${t.label} files are per settlement cycle).`];
  if (t.is_cycle_wise && row.cycle && !t.cycles.includes(row.cycle)) {
    return [`Cycle ${row.cycle} is not a cycle of ${t.label}.`];
  }
  return [];
}

/**
 * Notes that do not block the upload: the date in the file name differs from
 * the chosen business date (refused by the server), the file is too large,
 * CBS gets its date after processing (Amendment 1).
 */
export function rowNotes(
  row: ReviewRow,
  types: readonly FileTypeOption[],
  businessDate: string | null,
  maxFileBytes: number,
): string[] {
  const notes: string[] = [];
  const t = typeOf(types, row.typeId);
  const nameDate = row.detected.proposal?.business_date ?? null;
  if (t?.date_mode === 'EXACT_MATCH' && nameDate && businessDate && nameDate !== businessDate) {
    notes.push(
      `The file name carries ${formatDate(nameDate)}, not the chosen business date ${formatDate(businessDate)}: it will be refused.`,
    );
  }
  if (row.file.size > maxFileBytes) {
    notes.push(`The file is larger than the limit of ${Math.round(maxFileBytes / (1024 * 1024))} MB: it will be refused.`);
  }
  if (t?.date_mode === 'RANGE_CONTAINS') {
    notes.push('After processing, confirm its business date from the dates found in the file.');
  }
  if (row.changed && row.detected.proposal && t && t.id !== row.detected.proposal.file_type_config_id) {
    notes.push(`Changed from the proposed ${row.detected.proposal.label}; checked again on upload.`);
  }
  return notes;
}

/** Totals for the review summary and the upload button. */
export function reviewTotals(rows: readonly ReviewRow[], types: readonly FileTypeOption[]) {
  const included = rows.filter((r) => r.include);
  return {
    included: included.length,
    blocked: included.filter((r) => rowProblems(r, types).length > 0).length,
    duplicates: rows.filter((r) => r.detected.duplicate).length,
    unrecognised: rows.filter((r) => !r.detected.proposal).length,
  };
}

// ---------------------------------------------------------------------------
// hashing (duplicate warning before upload)
// ---------------------------------------------------------------------------
/**
 * The file's SHA-256 as hex, or "" when the browser offers no Web Crypto:
 * crypto.subtle exists only on HTTPS or localhost (P9: HTTPS for the
 * intranet address is a question for IT). Without it the duplicate is still
 * refused at upload, only not warned about in the review.
 */
export async function sha256Hex(file: Blob): Promise<string> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return '';
  try {
    const digest = await subtle.digest('SHA-256', await file.arrayBuffer());
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
  } catch {
    return '';
  }
}

// ---------------------------------------------------------------------------
// upload, one file per request
// ---------------------------------------------------------------------------
export interface UploadOutcome {
  index: number;
  filename: string;
  accepted: UploadedFileRow | null;
  errors: string[];
}

/**
 * Uploads the included rows ONE FILE PER REQUEST, in order, so the screen
 * can show progress and one slow or failed file (the ~100 MB CBS) does not
 * hold back or fail the others. The API accepts several files per request
 * too; each file is checked on its own either way. Stops (throws) on 401 /
 * 403, which would refuse every file; any other failure is that file's.
 */
export async function uploadRows(
  module: string,
  businessDate: string,
  rows: readonly ReviewRow[],
  onProgress: (done: number, total: number, current: string) => void,
  upload: typeof uploadFiles = uploadFiles,
): Promise<UploadOutcome[]> {
  const todo = rows.filter((r) => r.include && r.typeId !== null);
  const out: UploadOutcome[] = [];
  for (const [i, row] of todo.entries()) {
    onProgress(i, todo.length, row.file.name);
    let result: UploadResult;
    try {
      result = await upload(module, businessDate, [
        { file: row.file, item: { file_type_config_id: row.typeId as number, cycle: row.cycle } },
      ]);
    } catch (e) {
      if (e instanceof ApiError && (e.isUnauthenticated || e.isForbidden)) throw e;
      const msg = e instanceof ApiError ? e.detail : 'The file could not be sent (network error).';
      out.push({ index: row.index, filename: row.file.name, accepted: null, errors: [msg] });
      continue;
    }
    const accepted = result.accepted[0] ?? null;
    const refused = result.refused[0];
    out.push({
      index: row.index,
      filename: row.file.name,
      accepted,
      errors: accepted ? [] : (refused?.errors ?? [result.detail ?? 'Refused.']),
    });
  }
  onProgress(todo.length, todo.length, '');
  return out;
}
