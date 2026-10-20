import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DetectResult, ModuleSummary, RoleName } from '../api/types';
import { MeProvider } from '../auth/auth';
import { FILE_TYPES, makeFileRow, makeMe, renderWithProviders } from '../test/fixtures';
import { operationsTiles } from './HomePage';
import { UploadPage } from './UploadPage';

const RAW = 'UPIRAWDATAACQSSK140726_1C.csv';
const CBS = 'cbs_14072026.xls';

const DETECT: DetectResult = {
  business_date: '2026-07-14',
  business_dates_found: ['2026-07-14'],
  warnings: [],
  files: [
    {
      index: 0,
      filename: RAW,
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
      filename: CBS,
      detected_format: 'SPREADSHEETML_XML',
      proposal: null,
      duplicate: null,
      warnings: ['The file type could not be recognised. Choose it.'],
    },
  ],
  file_types: FILE_TYPES,
  max_file_bytes: 150 * 1024 * 1024,
  detect_head_bytes: 65536,
};

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function renderUpload(roles: RoleName[] = ['Operations User']) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path="/:module/files/upload" element={<UploadPage />} />
      </Routes>
    </MeProvider>,
    { route: '/upi/files/upload' },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Upload screen (design 4.8 / P5 c / Amendment 1)', () => {
  it('checks the files, lets the user correct a type, uploads and shows per-file results', async () => {
    document.cookie = 'csrftoken=tok';
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(200, DETECT))
      .mockResolvedValueOnce(json(201, { business_date: '2026-07-14', accepted: [makeFileRow({ id: 21 })], refused: [] }))
      .mockResolvedValueOnce(
        json(400, {
          business_date: '2026-07-14',
          accepted: [],
          refused: [{ index: 0, filename: CBS, errors: ['The content does not match DRC.'] }],
          detail: 'No file was accepted.',
        }),
      );
    vi.stubGlobal('fetch', fetch);
    renderUpload();

    await userEvent.upload(screen.getByLabelText('Choose files'), [new File(['a,b'], RAW), new File(['<x/>'], CBS)]);
    await userEvent.click(screen.getByRole('button', { name: 'Check files' }));

    const table = await screen.findByRole('table', { name: 'Files to upload' });
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/upi/files/detect/');
    const sent = init.body as FormData;
    expect(sent.getAll('files').map((f) => (f as File).name)).toEqual([RAW, CBS]);
    expect((sent.getAll('sha256')[0] as string).length).toBe(64);
    expect((init.headers as Record<string, string>)['X-CSRFToken']).toBe('tok');
    expect((screen.getByLabelText(/Business date of this upload/) as HTMLInputElement).value).toBe('14-Jul-2026');

    // The unrecognised CBS row blocks the upload until a type is chosen.
    expect(within(table).getByText('Choose the file type.')).toBeTruthy();
    const uploadButton = screen.getByRole('button', { name: 'Upload 2 file(s)' }) as HTMLButtonElement;
    expect(uploadButton.disabled).toBe(true);
    await userEvent.selectOptions(screen.getByLabelText(`File type for ${CBS}`), '2');
    expect(uploadButton.disabled).toBe(false);
    await userEvent.click(uploadButton);

    const results = await screen.findByRole('table', { name: 'Upload results' });
    expect(screen.getByText(/1 accepted, 1 refused/)).toBeTruthy();
    expect(within(results).getByRole('link', { name: RAW }).getAttribute('href')).toBe('/upi/files/21');
    expect(within(results).getByText('The content does not match DRC.')).toBeTruthy();

    // One file per request, each with its item and the batch business date.
    const form = (n: number) => (fetch.mock.calls[n]?.[1] as RequestInit).body as FormData;
    expect(fetch.mock.calls[1]?.[0]).toBe('/api/v1/upi/files/');
    expect(JSON.parse(form(1).get('items') as string)).toEqual([{ file_type_config_id: 3, cycle: '1C' }]);
    expect(JSON.parse(form(2).get('items') as string)).toEqual([{ file_type_config_id: 2, cycle: null }]);
    expect(form(1).get('business_date')).toBe('2026-07-14');
  });

  it('tells other roles that uploading is for the Operations User', () => {
    renderUpload(['Administrator']);
    expect(screen.getByText(/are for the Operations User/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Check files' })).toBeNull();
  });
});

describe('Home operations counts (design 4.3, Slice 2)', () => {
  it('shows the missing-files count and links to the files and the date', () => {
    const s: ModuleSummary = {
      business_date_today: '2026-10-19',
      roles: ['Operations User'],
      operations: {
        latest_business_date: '2026-07-14',
        files_received: 59,
        files_loaded: 58,
        files_failed: 0,
        files_missing: 8,
        awaiting_date_selection: 1,
        settlement_status: null,
      },
    };
    const tiles = Object.fromEntries(operationsTiles(s).map((t) => [t.key, t]));
    expect(tiles.files_missing?.value).toBe(8);
    expect(tiles.files_missing?.href).toBe('dates/2026-07-14');
    expect(tiles.files_missing?.note).toBeUndefined();
    expect(tiles.files_received?.href).toBe('files?business_date=2026-07-14');
    expect(tiles.files_failed?.href).toBe('files?business_date=2026-07-14&status=VALIDATION_FAILED%2CFAILED');
    expect(tiles.awaiting_date_selection?.href).toBe('files?status=AWAITING_DATE_SELECTION');
  });
});
