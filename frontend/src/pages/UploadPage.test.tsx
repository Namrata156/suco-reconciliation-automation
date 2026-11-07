import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DetectResult, ModuleSummary, RoleName } from '../api/types';
import { MeProvider } from '../auth/auth';
import { FILE_TYPES, makeFileRow, makeMe, renderWithProviders, routeFetch } from '../test/fixtures';
import { allLoaded, liveCounts } from '../features/files/UploadProgress';
import { checkerTiles, HomePage, makerTiles, operationsTiles, summaryBusinessDate } from './HomePage';
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
  vi.useRealTimers();
});

const TOO_LATE = 'Business date must be before today: ops reconcile the previous day.';

/** DETECT for the raw file only, proposing the given business date. */
const detectOne = (businessDate: string): DetectResult => ({
  ...DETECT,
  business_date: businessDate,
  business_dates_found: [businessDate],
  files: [{ ...DETECT.files[0]!, proposal: { ...DETECT.files[0]!.proposal!, business_date: businessDate } }],
});

async function checkOneFile() {
  await userEvent.upload(screen.getByLabelText('Choose files'), [new File(['a,b'], RAW)]);
  await userEvent.click(screen.getByRole('button', { name: 'Check files' }));
  await screen.findByRole('table', { name: 'Files to upload' });
}

describe('Upload screen (design 4.8 / P5 c / Amendment 1)', () => {
  it('checks the files, lets the user correct a type, uploads and shows per-file results', async () => {
    document.cookie = 'csrftoken=tok';
    const fetch = vi
      .fn()
      // After the upload the screen polls the accepted files (U4 2.2).
      .mockResolvedValue(
        json(200, { count: 0, next: null, previous: null, results: [], processing_count: 0 }),
      )
      .mockResolvedValueOnce(json(200, DETECT))
      .mockResolvedValueOnce(
        json(201, { business_date: '2026-07-14', accepted: [makeFileRow({ id: 21 })], refused: [] }),
      )
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

    await userEvent.upload(screen.getByLabelText('Choose files'), [
      new File(['a,b'], RAW),
      new File(['<x/>'], CBS),
    ]);
    await userEvent.click(screen.getByRole('button', { name: 'Check files' }));

    const table = await screen.findByRole('table', { name: 'Files to upload' });
    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/upi/files/detect/');
    const sent = init.body as FormData;
    expect(sent.getAll('files').map((f) => (f as File).name)).toEqual([RAW, CBS]);
    expect((sent.getAll('sha256')[0] as string).length).toBe(64);
    expect((init.headers as Record<string, string>)['X-CSRFToken']).toBe('tok');
    expect((screen.getByLabelText(/Business date of this upload/) as HTMLInputElement).value).toBe(
      '14-Jul-2026',
    );

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

  it('refuses a business date on or after today before sending (pre-filled from the file names)', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z')); // 23 Oct IST
    const fetch = vi.fn().mockResolvedValueOnce(json(200, detectOne('2026-10-23')));
    vi.stubGlobal('fetch', fetch);
    renderUpload();
    await checkOneFile();
    expect(screen.getByText(TOO_LATE)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Upload 1 file(s)' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(fetch).toHaveBeenCalledTimes(1); // detect only
  });

  it('the date picker stops at yesterday (IST)', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z'));
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json(200, detectOne('2026-10-21'))));
    renderUpload();
    await checkOneFile();
    await userEvent.click(screen.getByLabelText(/Business date of this upload/));
    const day = (label: string) =>
      screen.getByRole('button', { name: label, hidden: true }) as HTMLButtonElement;
    await screen.findByRole('button', { name: '22 October 2026', hidden: true });
    expect(day('22 October 2026').disabled).toBe(false);
    expect(day('23 October 2026').disabled).toBe(true);
    expect(day('24 October 2026').disabled).toBe(true);
  });

  it('shows the server refusal of the business date under the date and stops the upload', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-23T06:00:00Z'));
    const two = {
      ...DETECT,
      files: DETECT.files.map((f) => ({ ...f, proposal: f.proposal ?? DETECT.files[0]!.proposal })),
    };
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(200, { ...two, business_date: '2026-10-22' }))
      .mockResolvedValueOnce(json(400, { detail: 'Invalid input', errors: { business_date: [TOO_LATE] } }));
    vi.stubGlobal('fetch', fetch);
    renderUpload();
    await userEvent.upload(screen.getByLabelText('Choose files'), [
      new File(['a,b'], RAW),
      new File(['<x/>'], CBS),
    ]);
    await userEvent.click(screen.getByRole('button', { name: 'Check files' }));
    await screen.findByRole('table', { name: 'Files to upload' });
    await userEvent.click(screen.getByRole('button', { name: 'Upload 2 file(s)' }));
    expect(await screen.findByText(TOO_LATE)).toBeTruthy();
    // Back on the review, nothing more sent: the date refuses every file.
    expect(screen.getByRole('table', { name: 'Files to upload' })).toBeTruthy();
    expect(screen.queryByRole('table', { name: 'Upload results' })).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('a drop (files or a folder) chooses the files, like the pickers (U4 2.4)', async () => {
    renderUpload();
    const zone = screen.getByRole('button', { name: /Drop the day's files or folder here/ });
    fireEvent.drop(zone, {
      dataTransfer: { files: [new File(['a'], RAW), new File(['b'], CBS)], items: [], types: ['Files'] },
    });
    expect(await screen.findByText('2 file(s) chosen.')).toBeTruthy();
    // The pickers stay as the fallback.
    expect(screen.getByLabelText('Choose files')).toBeTruthy();
    expect(screen.getByLabelText('Or choose a folder')).toBeTruthy();
  });

  it('after the upload: live status, the CBS date confirmed here, then "Go to <date>" (U4 2.2 / 2.3)', async () => {
    document.cookie = 'csrftoken=tok';
    const raw = makeFileRow({ id: 31, state: 'PROCESSING', processing_status: 'STAGED' });
    const cbs = makeFileRow({
      id: 32,
      filename: CBS,
      business_date: null,
      state: 'PROCESSING',
      processing_status: 'RECEIVED',
      file_type: { id: 2, file_type_code: 'CBS', version: 1, register_type: 'CBS', label: 'CBS GL Extract' },
      cycle: null,
    });
    let confirmed = false;
    let uploads = 0;
    const polls: string[] = [];
    const cbsNow = () =>
      confirmed
        ? { ...cbs, business_date: '2026-07-14', state: 'LOADED', processing_status: 'LOADED' }
        : {
            ...cbs,
            state: 'AWAITING_DATE_SELECTION',
            processing_status: 'AWAITING_DATE_SELECTION',
            date_selection: {
              detected_dates: ['2026-07-13', '2026-07-14'],
              batch_business_date: '2026-07-14',
              preselected: '2026-07-14',
            },
          };
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/files/detect/': () => json(200, DETECT),
        '/files/': (init: RequestInit | undefined, url: string) => {
          if (init?.method === 'POST') {
            uploads += 1;
            return json(201, {
              business_date: '2026-07-14',
              accepted: [uploads === 1 ? raw : cbs],
              refused: [],
            });
          }
          // One poll for every file just sent (?ids=, contract E2).
          polls.push(url);
          return json(200, {
            count: 2,
            next: null,
            previous: null,
            processing_count: 0,
            results: [{ ...raw, state: 'LOADED', processing_status: 'LOADED' }, cbsNow()],
          });
        },
        '/files/32/select-date/': () => {
          confirmed = true;
          return json(200, {
            ...cbs,
            business_date: '2026-07-14',
            state: 'LOADED',
            processing_status: 'LOADED',
          });
        },
      }),
    );
    renderUpload();
    await userEvent.upload(screen.getByLabelText('Choose files'), [
      new File(['a,b'], RAW),
      new File(['<x/>'], CBS),
    ]);
    await userEvent.click(screen.getByRole('button', { name: 'Check files' }));
    await screen.findByRole('table', { name: 'Files to upload' });
    await userEvent.selectOptions(screen.getByLabelText(`File type for ${CBS}`), '2');
    await userEvent.click(screen.getByRole('button', { name: 'Upload 2 file(s)' }));

    // The CBS file asks for its date right here, pre-selected as on the file page.
    const confirm = await screen.findByRole('button', { name: 'Confirm 14-Jul-2026' });
    expect(screen.queryByRole('link', { name: 'Go to 14-Jul-2026' })).toBeNull();
    await userEvent.click(confirm);
    expect(await screen.findByRole('link', { name: 'Go to 14-Jul-2026' })).toBeTruthy();
    const results = screen.getByRole('table', { name: 'Upload results' });
    await waitFor(() => expect(within(results).getAllByText('Loaded')).toHaveLength(2));
    expect(polls[0]).toContain('ids=31%2C32');
  });

  it('review step: expected vs. in this batch vs. still missing, read again for another date (U4 2.1)', async () => {
    const checklist = (date: string, missing: number) => ({
      business_date: date,
      summary: {
        expected: 3,
        already_present: 1,
        in_batch: 1,
        missing_after_batch: missing,
        outside_checklist: 0,
      },
      slots: [
        {
          file_type_config_id: 3,
          file_type_code: 'RAW_DATA',
          label: 'Raw Data ACQ',
          cycle: '1C',
          state: 'MISSING',
          group: 'IN_BATCH',
          batch_files: [0],
          existing_files: [],
        },
        {
          file_type_config_id: 4,
          file_type_code: 'RAW_DATA',
          label: 'Raw Data ISS',
          cycle: '1C',
          state: 'MISSING',
          group: 'MISSING',
          batch_files: [],
          existing_files: [],
        },
      ],
      outside_checklist: [],
    });
    const fetch = vi.fn((_url: string, init?: RequestInit) => {
      const date = (init?.body as FormData).get('business_date');
      return Promise.resolve(
        json(200, {
          ...detectOne('2026-07-14'),
          checklist: date ? checklist(String(date), 2) : checklist('2026-07-14', 1),
        }),
      );
    });
    vi.stubGlobal('fetch', fetch);
    renderUpload();
    await checkOneFile();
    expect(screen.getByRole('heading', { name: 'Expected files for 14-Jul-2026' })).toBeTruthy();
    expect(
      screen.getByText('3 expected: 1 already here, 1 in this batch, 1 still missing after this upload.'),
    ).toBeTruthy();
    const missing = screen
      .getByText(/Still missing after this upload: 1/)
      .closest('.mantine-Alert-root') as HTMLElement;
    expect(within(missing).getByText('Raw Data ISS 1C')).toBeTruthy();

    const dateBox = screen.getByLabelText(/Business date of this upload/);
    await userEvent.clear(dateBox);
    await userEvent.type(dateBox, '13-Jul-2026');
    await userEvent.tab();
    expect(await screen.findByRole('heading', { name: 'Expected files for 13-Jul-2026' })).toBeTruthy();
    const again = fetch.mock.calls.at(-1)?.[1]?.body as FormData;
    expect(again.get('business_date')).toBe('2026-07-13');
  });

  it('counts the live states; "Go to" only when every accepted file is loaded', () => {
    expect(liveCounts(['PROCESSING', 'LOADED', 'FAILED', 'AWAITING_DATE_SELECTION'])).toEqual({
      processing: 1,
      loaded: 1,
      failed: 1,
      awaiting: 1,
    });
    expect(allLoaded(['LOADED', 'LOADED'])).toBe(true);
    expect(allLoaded(['LOADED', 'AWAITING_DATE_SELECTION'])).toBe(false);
    expect(allLoaded([])).toBe(false);
  });

  it('tells other roles that uploading is for the Operations User', () => {
    renderUpload(['Administrator']);
    expect(screen.getByText(/are for the Operations User/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Check files' })).toBeNull();
  });
});

describe('Home heading: the business date, not today', () => {
  it('uses business_date, else business_date_today minus one day', () => {
    expect(summaryBusinessDate({ business_date_today: '2026-10-23', business_date: '2026-10-22' })).toBe(
      '2026-10-22',
    );
    expect(summaryBusinessDate({ business_date_today: '2026-11-01' })).toBe('2026-10-31');
  });

  it('shows "My work - business date 22-Oct-2026"', async () => {
    const summary: ModuleSummary = {
      business_date_today: '2026-10-23',
      business_date: '2026-10-22',
      roles: ['Maker'],
    };
    const fetch = vi.fn().mockResolvedValue(json(200, summary));
    vi.stubGlobal('fetch', fetch);
    renderWithProviders(
      <MeProvider me={makeMe(['Maker'])}>
        <Routes>
          <Route path="/:module" element={<HomePage />} />
        </Routes>
      </MeProvider>,
      { route: '/upi' },
    );
    expect(await screen.findByRole('heading', { name: 'My work - business date 22-Oct-2026' })).toBeTruthy();
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/v1/upi/summary/');
  });

  it('falls back to business_date_today minus one day from an older server', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(json(200, { business_date_today: '2026-10-23', roles: ['Maker'] })),
    );
    renderWithProviders(
      <MeProvider me={makeMe(['Maker'])}>
        <Routes>
          <Route path="/:module" element={<HomePage />} />
        </Routes>
      </MeProvider>,
      { route: '/upi' },
    );
    expect(await screen.findByRole('heading', { name: 'My work - business date 22-Oct-2026' })).toBeTruthy();
  });
});

describe('Home maker / checker counts', () => {
  it('leaves out the HIGH priority counts the server still sends', () => {
    const n = (count: number) => ({ count, query: { status: 'OPEN' } });
    const s: ModuleSummary = {
      business_date_today: '2026-10-19',
      roles: ['Maker', 'Checker'],
      maker: {
        high_open: n(3),
        high_overdue: n(1),
        overdue: n(5),
        due_today: n(2),
        approved_waiting: n(0),
        mine: n(4),
        mine_overdue: n(1),
      },
      checker: {
        awaiting: n(6),
        awaiting_high: n(2),
        awaiting_submitted_by_me: n(1),
        needs_review: n(7),
        settings_awaiting: { response_codes: 0, ntsl_mapping: 0, tolerance: 0 },
      },
    };
    expect(makerTiles(s).map((t) => t.key)).toEqual([
      'overdue',
      'due_today',
      'approved_waiting',
      'mine',
      'mine_overdue',
    ]);
    expect(checkerTiles(s).map((t) => t.key)).toEqual([
      'awaiting',
      'awaiting_submitted_by_me',
      'needs_review',
      'settings_awaiting',
    ]);
    expect([...makerTiles(s), ...checkerTiles(s)].some((t) => /HIGH/.test(t.label))).toBe(false);
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

  it('greys out 0 counts in place; the others stay bold (U1 1.3)', async () => {
    const summary: ModuleSummary = {
      business_date_today: '2026-10-19',
      roles: ['Operations User'],
      operations: {
        latest_business_date: '2026-07-14',
        files_received: 59,
        files_loaded: 59,
        files_failed: 0,
        files_missing: 0,
        awaiting_date_selection: 1,
        settlement_status: null,
      },
    };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(200, summary)));
    renderWithProviders(
      <MeProvider me={makeMe(['Operations User'])}>
        <Routes>
          <Route path="/:module" element={<HomePage />} />
        </Routes>
      </MeProvider>,
      { route: '/upi' },
    );
    const failed = await screen.findByRole('link', { name: 'Files failed (14-Jul-2026): 0. Open the list' });
    const received = screen.getByRole('link', { name: 'Files received (14-Jul-2026): 59. Open the list' });
    expect(failed.closest('[data-zero]')).toBeTruthy();
    expect(received.closest('[data-zero]')).toBeNull();
    // Same order as before: the 0 tiles keep their position.
    const labels = screen.getAllByRole('link').map((a) => a.getAttribute('aria-label'));
    expect(labels.map((l) => l?.split(' (')[0]?.split(':')[0])).toEqual([
      'Files received',
      'Files loaded',
      'Files failed',
      'Expected files missing',
      'CBS files awaiting date selection',
    ]);
  });
});
