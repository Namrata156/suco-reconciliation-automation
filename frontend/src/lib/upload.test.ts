import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../api/client';
import type { DetectResult, UploadResult } from '../api/types';
import { FILE_TYPES, makeFileRow } from '../test/fixtures';
import { buildRows, reviewTotals, rowNotes, rowProblems, sha256Hex, uploadRows, withCycle, withType } from './upload';

const file = (name: string, size = 10) => new File(['x'.repeat(size)], name);

function detect(over: Partial<DetectResult> = {}): DetectResult {
  return {
    business_date: '2026-07-14',
    business_dates_found: ['2026-07-14'],
    warnings: [],
    files: [
      {
        index: 0,
        filename: 'UPIRAWDATAACQSSK140726_1C.csv',
        detected_format: 'CSV',
        proposal: {
          file_type_config_id: 3,
          file_type_code: 'RAW_DATA',
          label: 'Raw Data ACQ',
          cycle: '1C',
          business_date: '2026-07-14',
          date_mode: 'EXACT_MATCH',
          matched_by: 'filename',
        },
        duplicate: null,
        warnings: [],
      },
      {
        index: 1,
        filename: 'cbs_14072026.xls',
        detected_format: 'SPREADSHEETML_XML',
        proposal: {
          file_type_config_id: 1,
          file_type_code: 'CBS_GL_EXTRACT',
          label: 'CBS GL Extract',
          cycle: null,
          business_date: null,
          date_mode: 'RANGE_CONTAINS',
          matched_by: 'content',
        },
        duplicate: null,
        warnings: ['This file covers several dates.'],
      },
      {
        index: 2,
        filename: 'notes.csv',
        detected_format: 'CSV',
        proposal: null,
        duplicate: {
          uploaded_file_id: 4,
          filename: 'notes.csv',
          uploaded_at: '2026-07-15T04:30:00Z',
          uploaded_by: 'ops1',
          message: 'Already uploaded on 15-Jul-2026 10:00 by ops1.',
        },
        warnings: ['The file type could not be recognised. Choose it.'],
      },
    ],
    file_types: FILE_TYPES,
    max_file_bytes: 150 * 1024 * 1024,
    detect_head_bytes: 65536,
    ...over,
  };
}

const picked = () =>
  ['UPIRAWDATAACQSSK140726_1C.csv', 'cbs_14072026.xls', 'notes.csv'].map((n) => ({ file: file(n), sha256: '' }));

describe('upload review rows (design 4.8 / Amendment 1)', () => {
  it('takes the proposals and leaves duplicates unticked', () => {
    const [raw, cbs, notes] = buildRows(picked(), detect());
    expect([raw?.typeId, raw?.cycle, raw?.include]).toEqual([3, '1C', true]);
    expect([cbs?.typeId, cbs?.cycle]).toEqual([1, null]);
    expect([notes?.typeId, notes?.include]).toEqual([null, false]);
    expect(reviewTotals(buildRows(picked(), detect()), FILE_TYPES)).toEqual({
      included: 2,
      blocked: 0,
      duplicates: 1,
      unrecognised: 1,
    });
  });

  it('blocks an included row without a type or cycle', () => {
    const rows = buildRows(picked(), detect());
    const notes = { ...rows[2]!, include: true };
    expect(rowProblems(notes, FILE_TYPES)).toEqual(['Choose the file type.']);
    const raw = withCycle(rows[0]!, '');
    expect(rowProblems(raw, FILE_TYPES)[0]).toMatch(/Choose the cycle/);
    expect(rowProblems({ ...raw, include: false }, FILE_TYPES)).toEqual([]);
  });

  it('keeps the cycle when the new type has it, drops it for a daily type', () => {
    const raw = buildRows(picked(), detect())[0]!;
    expect(withType(raw, 4, FILE_TYPES).cycle).toBe('1C'); // Raw Data ISS has 1C too
    expect(withType(raw, 2, FILE_TYPES).cycle).toBeNull(); // DRC is daily
    expect(withType(withCycle(raw, '9C'), 5, FILE_TYPES).cycle).toBe('9C');
  });

  it('warns that a file whose name carries another date will be refused', () => {
    const raw = buildRows(picked(), detect())[0]!;
    expect(rowNotes(raw, FILE_TYPES, '2026-07-14', 1e9)).toEqual([]);
    expect(rowNotes(raw, FILE_TYPES, '2026-07-15', 1e9)[0]).toMatch(
      /carries 14-Jul-2026, not the chosen business date 15-Jul-2026: it will be refused/,
    );
    expect(rowNotes(raw, FILE_TYPES, '2026-07-14', 5)[0]).toMatch(/larger than the limit/);
  });

  it('tells the user CBS gets its date after processing', () => {
    const cbs = buildRows(picked(), detect())[1]!;
    expect(rowNotes(cbs, FILE_TYPES, '2026-07-14', 1e9)).toEqual([
      'After processing, confirm its business date from the dates found in the file.',
    ]);
  });
});

describe('uploadRows (one file per request)', () => {
  const ok = (id: number): UploadResult => ({
    business_date: '2026-07-14',
    accepted: [makeFileRow({ id })],
    refused: [],
  });

  it('sends each included file with its type and cycle and collects the results', async () => {
    const rows = buildRows(picked(), detect());
    const upload = vi
      .fn()
      .mockResolvedValueOnce(ok(11))
      .mockResolvedValueOnce({
        business_date: '2026-07-14',
        accepted: [],
        refused: [{ index: 0, filename: 'cbs_14072026.xls', errors: ['Already uploaded on ...'] }],
        detail: 'No file was accepted.',
      });
    const progress = vi.fn();
    const out = await uploadRows('upi', '2026-07-14', rows, progress, upload);
    expect(upload).toHaveBeenCalledTimes(2); // the unticked duplicate is not sent
    expect(upload.mock.calls[0]?.[2][0].item).toEqual({ file_type_config_id: 3, cycle: '1C' });
    expect(upload.mock.calls[1]?.[2][0].item).toEqual({ file_type_config_id: 1, cycle: null });
    expect(out.map((o) => [o.filename, o.accepted?.id ?? null, o.errors])).toEqual([
      ['UPIRAWDATAACQSSK140726_1C.csv', 11, []],
      ['cbs_14072026.xls', null, ['Already uploaded on ...']],
    ]);
    expect(progress).toHaveBeenLastCalledWith(2, 2, '');
  });

  it("records another file's error and continues; stops on 403", async () => {
    const rows = buildRows(picked(), detect());
    const upload = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(409, { detail: 'Business date 2026-07-14 has been archived.' }, ''))
      .mockResolvedValueOnce(ok(12));
    const out = await uploadRows('upi', '2026-07-14', rows, () => {}, upload);
    expect(out[0]?.errors).toEqual(['Business date 2026-07-14 has been archived.']);
    expect(out[1]?.accepted?.id).toBe(12);

    const forbidden = vi.fn().mockRejectedValue(new ApiError(403, { detail: 'Only an Operations User' }, ''));
    await expect(uploadRows('upi', '2026-07-14', rows, () => {}, forbidden)).rejects.toThrow(
      'Only an Operations User',
    );
    expect(forbidden).toHaveBeenCalledTimes(1);
  });
});

describe('sha256Hex', () => {
  it('hashes with Web Crypto', async () => {
    expect(await sha256Hex(new Blob(['abc']))).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });

  it('gives "" without Web Crypto (plain-HTTP intranet address)', async () => {
    vi.stubGlobal('crypto', {});
    try {
      expect(await sha256Hex(new Blob(['abc']))).toBe('');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
