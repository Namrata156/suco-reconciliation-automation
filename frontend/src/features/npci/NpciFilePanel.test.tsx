import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  BusinessDateDetail,
  NpciFileDetail,
  NpciFilePreview,
  NpciFileRow,
  NpciNotIncluded,
  RoleName,
} from '../../api/types';
import { MeProvider } from '../../auth/auth';
import { DateDetailPage } from '../../pages/DateDetailPage';
import {
  json,
  makeDateOverview,
  makeMe,
  META,
  renderWithProviders,
  routeFetch,
  sentBody,
} from '../../test/fixtures';
import { needsPartialConfirmation, notIncludedSummary, replyCode } from './NpciFilePanel';
import { ApiError } from '../../api/client';

const DATE = '2026-07-14';
const NAME = 'SSKUPI14072026.csv';

function detail(): BusinessDateDetail {
  return {
    ...makeDateOverview({ cases: { total: 3, active: 3 } }),
    checklist: [],
    unexpected_files: [],
    archived: false,
    runs: [],
    history: [],
  };
}

const left = (o: Partial<NpciNotIncluded> = {}): NpciNotIncluded => ({
  case_id: 301,
  case_business_date: DATE,
  rrn: '419500000301',
  rule_code: 'R01',
  status: 'OPEN',
  flag: 'DRC',
  code: '102',
  reason: 'OPEN',
  reason_label: 'Not submitted yet (open or in progress)',
  detail: '',
  ...o,
});

function preview(o: Partial<NpciFilePreview> = {}): NpciFilePreview {
  return {
    file_date: DATE,
    file_format: 'SSKUPI',
    next_sequence: 1,
    next_file_name: NAME,
    row_count: 1,
    total_amount: '140.50',
    partial: false,
    counts: [{ flag: 'DRC', code: '102', text: 'Online Reversal', rows: 1, amount: '140.50' }],
    rows: [
      {
        line: 1,
        case_id: 201,
        case_business_date: DATE,
        rrn: '419500000201',
        rule_code: 'R01',
        action_type: 'DRC_102',
        flag: 'DRC',
        code: '102',
        shtdat: DATE,
        amount: '140.5',
        account_masked: '********1234',
        text: 'Online Reversal',
      },
    ],
    not_included: [],
    not_included_counts: {},
    already_sent_count: 0,
    already_sent_files: [],
    warnings: [],
    files: [],
    can_generate: true,
    ...o,
  };
}

const PARTIAL = {
  partial: true,
  not_included: [
    left(),
    left({
      case_id: 302,
      rrn: '419500000302',
      rule_code: 'R05',
      status: 'APPROVED',
      flag: 'RET',
      code: null,
      reason: 'NO_NUMERIC_CODE',
      reason_label: 'The reply code has no numeric NPCI code yet',
      detail: 'RET (unknown) - Account closed',
    }),
  ],
  not_included_counts: { NO_NUMERIC_CODE: 1, OPEN: 1 },
  warnings: [
    '1 case(s) left out because their reply code has no numeric NPCI code yet (RET (unknown) - Account closed: 1). ' +
      'They go into a later file automatically once the code is filled in.',
  ],
};

function fileRow(o: Partial<NpciFileRow> = {}): NpciFileRow {
  return {
    id: 7,
    file_date: DATE,
    sequence: 1,
    file_name: NAME,
    file_format: 'SSKUPI',
    row_count: 15,
    total_amount: '2100.00',
    partial: false,
    not_included_count: 0,
    sha256: 'ab'.repeat(32),
    size_bytes: 1500,
    generated_by: 'checker1',
    generated_at: '2026-07-15T06:00:00Z',
    remarks: '',
    download_url: '/api/v1/upi/npci-files/7/download/',
    downloads: [],
    download_count: 0,
    ...o,
  };
}

const fileDetail = (f: NpciFileRow): NpciFileDetail => ({
  ...f,
  counts: [],
  warnings: [],
  not_included: [],
  rows: [],
  other_files_for_date: [],
  history: [],
});

const filesPage = (results: NpciFileRow[], canDownload = true) => ({
  count: results.length,
  next: null,
  previous: null,
  results,
  can_download: canDownload,
});

const isPost = (init?: RequestInit) => init?.method === 'POST';
const generatePosts = (fetch: ReturnType<typeof routeFetch>) =>
  fetch.mock.calls.filter((c) => String(c[0]).endsWith('/npci-file/') && isPost(c[1] as RequestInit));

function renderDate(roles: RoleName[]) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path="/:module/dates/:businessDate" element={<DateDetailPage />} />
      </Routes>
    </MeProvider>,
    { route: `/upi/dates/${DATE}` },
  );
}

const baseRoutes = {
  [`/dates/${DATE}/`]: () => json(200, detail()),
  '/results/summary/': () => json(404, {}),
  '/meta/': META,
};

beforeEach(() => {
  // jsdom has no object URLs and no navigation; the click itself is what is checked.
  URL.createObjectURL = vi.fn(() => 'blob:npci');
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('NPCI file helpers', () => {
  it('names a reply without a numeric code', () => {
    expect(replyCode('RET', '115')).toBe('RET 115');
    expect(replyCode('RET', null)).toBe('RET (no numeric code yet)');
  });

  it('summarises what is left out per reason, in the screen order', () => {
    expect(notIncludedSummary(preview(PARTIAL))).toEqual([
      '1 case(s): Not submitted yet (open or in progress)',
      '1 case(s): The reply code has no numeric NPCI code yet',
    ]);
  });

  it('recognises the 409 that asks to confirm a partial file', () => {
    const e = new ApiError(409, { detail: 'x', errors: { confirm_partial: ['Confirm'] } }, '');
    expect(needsPartialConfirmation(e)).toBe(true);
    expect(needsPartialConfirmation(new ApiError(409, { errors: { rows: ['none'] } }, ''))).toBe(false);
  });
});

describe('NPCI file on the business date (A7, owner 2026-10-22/23)', () => {
  it('Checker generates a partial file only after confirming; left-out cases and warnings show', async () => {
    let generated = false;
    const fetch = routeFetch({
      ...baseRoutes,
      '/npci-file/preview/': () =>
        json(
          200,
          generated
            ? preview({
                ...PARTIAL,
                row_count: 0,
                rows: [],
                counts: [],
                can_generate: false,
                next_sequence: 2,
                next_file_name: 'SSKUPI14072026_2.csv',
              })
            : preview(PARTIAL),
        ),
      '/npci-file/': (init: RequestInit | undefined) => {
        if (!isPost(init)) return json(405, {});
        generated = true;
        return json(201, fileDetail(fileRow({ partial: true, not_included_count: 2, row_count: 1 })));
      },
      '/npci-files/': () =>
        json(200, filesPage(generated ? [fileRow({ partial: true, not_included_count: 2 })] : [])),
    });
    vi.stubGlobal('fetch', fetch);
    renderDate(['Checker']);

    expect(await screen.findByText(`Next file: ${NAME}`)).toBeTruthy();
    const left = screen.getByRole('table', { name: `Cases not included in ${NAME}` });
    expect(within(left).getByText('RET (no numeric code yet)')).toBeTruthy();
    expect(within(left).getByText('The reply code has no numeric NPCI code yet')).toBeTruthy();
    expect(within(left).getByRole('link', { name: '#302' }).getAttribute('href')).toBe('/upi/cases/302');
    expect(screen.getByText(`Not included in ${NAME}: 2 case(s)`)).toBeTruthy();
    expect(
      within(screen.getByRole('group', { name: 'NPCI file warnings' })).getByText(/no numeric NPCI code yet/),
    ).toBeTruthy();
    expect(screen.getByText('No file generated for this date yet.')).toBeTruthy();

    await userEvent.click(screen.getByRole('button', { name: `Generate ${NAME}` }));
    const dialog = await screen.findByRole('dialog', { name: `Generate ${NAME}?` });
    expect(within(dialog).getByText(/will be included in file SSKUPI14072026\.csv/)).toBeTruthy();
    expect(within(dialog).getByText('1 case(s): Not submitted yet (open or in progress)')).toBeTruthy();
    const go = within(dialog).getByRole('button', { name: 'Generate partial file' }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    await userEvent.click(
      within(dialog).getByRole('checkbox', { name: `I confirm that ${NAME} is a partial file` }),
    );
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Remarks (optional)' }), 'RET deadline');
    await userEvent.click(go);

    await waitFor(() => expect(generatePosts(fetch)).toHaveLength(1));
    expect(sentBody(generatePosts(fetch)[0])).toEqual({ confirm_partial: true, remarks: 'RET deadline' });
    expect(await screen.findByText(`${NAME} generated`)).toBeTruthy();
    // The list and the preview reload: the next file would be _2.
    expect(await screen.findByText('Next file: SSKUPI14072026_2.csv')).toBeTruthy();
    const files = await screen.findByRole('table', { name: 'NPCI files generated for 14-Jul-2026' });
    expect(within(files).getByText('Partial: 2 left out')).toBeTruthy();
    expect(screen.queryByText(/sent to NPCI/i)).toBeNull();
  });

  it('asks for the confirmation when the server answers 409 confirm_partial, then posts again', async () => {
    let posts = 0;
    const fetch = routeFetch({
      ...baseRoutes,
      // Complete when the page loaded; a case was reopened meanwhile.
      '/npci-file/preview/': () => json(200, posts ? preview(PARTIAL) : preview()),
      '/npci-file/': (init: RequestInit | undefined) => {
        if (!isPost(init)) return json(405, {});
        posts += 1;
        const body = sentBody([null, init]) as { confirm_partial: boolean };
        if (!body.confirm_partial)
          return json(409, {
            detail: '2 case(s) for this date are not included; confirm to generate a partial file',
            errors: { confirm_partial: ['Confirm that the file is partial to continue.'] },
          });
        return json(201, fileDetail(fileRow({ partial: true })));
      },
      '/npci-files/': () => json(200, filesPage([])),
    });
    vi.stubGlobal('fetch', fetch);
    renderDate(['Checker']);

    await userEvent.click(await screen.findByRole('button', { name: `Generate ${NAME}` }));
    let dialog = await screen.findByRole('dialog', { name: `Generate ${NAME}?` });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Generate file' }));
    dialog = await screen.findByRole('dialog', { name: `Generate ${NAME}?` });
    expect(await within(dialog).findByText(/confirm to generate a partial file/)).toBeTruthy();
    expect(
      await within(dialog).findByText('1 case(s): The reply code has no numeric NPCI code yet'),
    ).toBeTruthy();
    await userEvent.click(
      within(dialog).getByRole('checkbox', { name: `I confirm that ${NAME} is a partial file` }),
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Generate partial file' }));
    await waitFor(() => expect(generatePosts(fetch)).toHaveLength(2));
    expect(sentBody(generatePosts(fetch)[0])).toEqual({ confirm_partial: false, remarks: '' });
    expect(sentBody(generatePosts(fetch)[1])).toEqual({ confirm_partial: true, remarks: '' });
  });

  it('lists the files of the date with hash and download history; Download refreshes the list', async () => {
    let downloaded = 0;
    const first = fileRow({
      download_count: 2,
      downloads: [
        { by: 'ops1', at: '2026-07-15T07:00:00Z' },
        { by: 'maker1', at: '2026-07-15T06:30:00Z' },
      ],
    });
    const second = () =>
      fileRow({
        id: 8,
        sequence: 2,
        file_name: 'SSKUPI14072026_2.csv',
        row_count: 6,
        sha256: 'cd'.repeat(32),
        download_url: '/api/v1/upi/npci-files/8/download/',
        download_count: downloaded,
        downloads: downloaded ? [{ by: 'ops1', at: '2026-07-16T05:00:00Z' }] : [],
      });
    const fetch = routeFetch({
      ...baseRoutes,
      '/npci-file/preview/': () =>
        json(
          200,
          preview({
            next_sequence: 3,
            next_file_name: 'SSKUPI14072026_3.csv',
            row_count: 0,
            rows: [],
            counts: [],
            can_generate: false,
            already_sent_count: 21,
            already_sent_files: [NAME, 'SSKUPI14072026_2.csv'],
            warnings: [
              '1 case(s) already sent in an earlier file were changed by a re-run and are flagged for review.',
            ],
          }),
        ),
      // Newest first, as the server sends them.
      '/npci-files/': () => json(200, filesPage([second(), first])),
      '/npci-files/8/download/': () => {
        downloaded += 1;
        return new Response('SSKUPI14072026_2.csv,DRC\r\n', {
          status: 200,
          headers: { 'Content-Type': 'text/csv' },
        });
      },
    });
    vi.stubGlobal('fetch', fetch);
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    renderDate(['Operations User']);

    const files = await screen.findByRole('table', { name: 'NPCI files generated for 14-Jul-2026' });
    const rows = within(files).getAllByRole('row').slice(1);
    // In sequence order: the first file, then _2.
    expect(within(rows[0]!).getByText(NAME)).toBeTruthy();
    expect(within(rows[1]!).getByText('SSKUPI14072026_2.csv')).toBeTruthy();
    expect(within(rows[0]!).getByText('ab'.repeat(32))).toBeTruthy();
    expect(within(rows[0]!).getByText('checker1')).toBeTruthy();
    expect(within(rows[0]!).getByText('Downloaded 2 times')).toBeTruthy();
    expect(within(rows[0]!).getByText(/^Last: ops1, 15-Jul-2026 12:30 IST$/)).toBeTruthy();
    expect(within(rows[1]!).getByText('Not downloaded yet')).toBeTruthy();
    // The server's warning as it is, and what is already in a file.
    expect(screen.getByText(/changed by a re-run and are flagged for review/)).toBeTruthy();
    expect(
      screen.getByText(
        /21 case\(s\) of this date are already included in files SSKUPI14072026\.csv, SSKUPI14072026_2\.csv/,
      ),
    ).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Generate/ })).toBeNull();
    expect(screen.getByText('A Checker generates the file.')).toBeTruthy();

    await userEvent.click(within(rows[0]!).getByRole('button', { name: `All downloads of ${NAME}` }));
    const list = await screen.findByRole('list', { name: `Downloads of ${NAME}` });
    expect(within(list).getByText('maker1, 15-Jul-2026 12:00 IST')).toBeTruthy();

    await userEvent.click(within(rows[1]!).getByRole('button', { name: 'Download SSKUPI14072026_2.csv' }));
    await waitFor(() => expect(downloaded).toBe(1));
    expect(click).toHaveBeenCalled();
    expect(URL.createObjectURL).toHaveBeenCalled();
    // The list is read again and shows the new download.
    await waitFor(() =>
      expect(
        within(screen.getByRole('table', { name: 'NPCI files generated for 14-Jul-2026' })).getByText(
          'Downloaded once',
        ),
      ).toBeTruthy(),
    );
    expect(screen.queryByRole('button', { name: /Approve|Reject/ })).toBeNull();
  });

  it('Auditor sees the list only: no Generate, no Download', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        ...baseRoutes,
        '/npci-file/preview/': () => json(200, preview({ can_generate: false })),
        '/npci-files/': () => json(200, filesPage([fileRow({ download_url: null })], false)),
        '/npci-files/7/': () => json(200, fileDetail(fileRow({ download_url: null }))),
      }),
    );
    renderDate(['Auditor']);
    const files = await screen.findByRole('table', { name: 'NPCI files generated for 14-Jul-2026' });
    expect(screen.getByText(/Your role sees the list only/)).toBeTruthy();
    expect(within(files).queryByRole('button', { name: /Download/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Generate/ })).toBeNull();
    await userEvent.click(within(files).getByRole('button', { name: `Details of ${NAME}` }));
    const drawer = await screen.findByRole('dialog', { name: NAME });
    expect(await within(drawer).findByText('History (generation and downloads)')).toBeTruthy();
    expect(within(drawer).queryByRole('button', { name: /Download/ })).toBeNull();
  });

  it('says when the module has no NPCI file format (404)', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        ...baseRoutes,
        '/npci-file/preview/': () => json(404, { detail: 'No NPCI file format is defined for IMPS.' }),
      }),
    );
    renderDate(['Checker']);
    expect(await screen.findByText('No NPCI file format is defined for IMPS.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Generate/ })).toBeNull();
  });
});
