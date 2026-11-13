import { notifications } from '@mantine/notifications';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type {
  BusinessDateDetail,
  CreditVoucherDetail,
  CreditVoucherList,
  CreditVoucherPreview,
  CreditVoucherPreviewLine,
  CreditVoucherRow,
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
import { CreditVoucherPanel, revStep } from './CreditVoucherPanel';

const DATE = '2026-11-02';
const NAME = 'upi rev 02112026.csv';
const CV = `/api/v1/upi/dates/${DATE}/credit-voucher`;

const NEEDS = "No CBS account in the NPCI report: enter the customer's 16-digit account";

function line(o: Partial<CreditVoucherPreviewLine> = {}): CreditVoucherPreviewLine {
  return {
    line: 1,
    sr_no: 2,
    case_id: 501,
    case_business_date: DATE,
    case_status: 'OPEN',
    rule_code: 'R07r',
    action_type: 'REFUND_CUSTOMER',
    rrn: '530600000501',
    txn_date: '2026-11-01',
    amount: '250.00',
    account_masked: 'XXXXXXXXXXXX1234',
    check: '',
    narration: 'UPI reversal entry RRN/530600000501  Dated  1112026',
    ...o,
  };
}

const ROWS = [
  line(),
  line({
    line: 2,
    sr_no: 3,
    case_id: 502,
    rule_code: 'R02',
    rrn: '530600000502',
    account_masked: '',
    check: NEEDS,
  }),
];

function preview(o: Partial<CreditVoucherPreview> = {}): CreditVoucherPreview {
  return {
    voucher_date: DATE,
    next_version: 1,
    next_file_name: NAME,
    debit_account_masked: 'XXXX1535',
    narration: 'UPI TO RRC DRC TCC REVERSAL DATED ON NOV 02',
    row_count: 2,
    total_amount: '500.00',
    rows_to_check: 1,
    counts: [],
    rows: ROWS,
    not_included: [],
    not_included_counts: {},
    warnings: [
      '1 row(s) need an account number filled in or corrected (see the Check column) before the file is uploaded.',
    ],
    can_generate: true,
    ...o,
  };
}

function version(o: Partial<CreditVoucherRow> = {}): CreditVoucherRow {
  return {
    id: 11,
    voucher_date: DATE,
    version: 1,
    file_name: NAME,
    status: 'GENERATED',
    status_label: 'Generated, waiting for the checked file',
    row_count: 2,
    total_amount: '500.00',
    rows_to_check: 1,
    counts: [],
    not_included_count: 0,
    debit_account_masked: 'XXXX1535',
    narration: 'UPI TO RRC DRC TCC REVERSAL DATED ON NOV 02',
    sha256: 'ab'.repeat(32),
    size_bytes: 400,
    generated_by: 'suco',
    generated_at: '2026-11-03T05:00:00Z',
    remarks: '',
    downloads: [],
    download_count: 0,
    download_url: `${CV}/11/download/`,
    upload_url: `${CV}/11/upload/`,
    can_upload: true,
    approval: null,
    ...o,
  };
}

function voucherDetail(o: Partial<CreditVoucherDetail> = {}): CreditVoucherDetail {
  return {
    ...version(),
    rows: ROWS.map((r) => ({ ...r, approved: false, uploaded_account_masked: null, account_changed: null })),
    not_included: [],
    warnings: [],
    history: [],
    ...o,
  };
}

function list(versions: CreditVoucherRow[]): CreditVoucherList {
  return {
    voucher_date: DATE,
    results: versions,
    latest: versions[0] ?? null,
    pending_approval: versions[0]?.status === 'GENERATED',
    can_generate: true,
    can_download: true,
  };
}

const APPROVED = version({
  status: 'APPROVED',
  status_label: 'Approved (checked file uploaded)',
  upload_url: null,
  can_upload: false,
  download_count: 1,
  approval: {
    approved_by: 'suco',
    approved_at: '2026-11-03T06:00:00Z',
    uploaded_file_name: NAME,
    uploaded_sha256: 'cd'.repeat(32),
    approved_count: 1,
    left_open_count: 1,
    total_amount: '250.00',
  },
});

function renderPanel() {
  return renderWithProviders(<CreditVoucherPanel module="upi" businessDate={DATE} />, {
    route: `/upi/dates/${DATE}`,
  });
}

const urlsOf = (f: ReturnType<typeof routeFetch>) => f.mock.calls.map((c) => String(c[0]));

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => 'blob:rev');
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  notifications.clean();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('rev credit file step', () => {
  it('none generated yet: the next file, its rows with the "needs account" rows highlighted, Generate', async () => {
    let generated = 0;
    const fetch = routeFetch({
      '/credit-voucher/preview/': preview(),
      '/credit-voucher/': (init: RequestInit | undefined) => {
        if (init?.method === 'POST') {
          generated += 1;
          return json(201, voucherDetail());
        }
        return json(200, generated ? list([version()]) : list([]));
      },
      '/credit-voucher/11/': voucherDetail(),
    });
    vi.stubGlobal('fetch', fetch);
    renderPanel();

    expect(await screen.findByText('No rev credit file generated for this date yet.')).toBeTruthy();
    expect(
      screen.getByText(
        /2 credit row\(s\) ready for upi rev 02112026\.csv \(Rs 500\.00\)\. 1 of them need an account\./,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText(/Uploading the checked file approves and closes the cases it lists/),
    ).toBeTruthy();
    // Rows are open because one needs an account; that row is highlighted.
    const table = screen.getByRole('table', { name: `Rows of ${NAME}` });
    const needs = within(table).getByText('Needs account').closest('tr')!;
    expect(needs.getAttribute('style') ?? '').toContain('orange');
    expect(within(needs).getByText(NEEDS)).toBeTruthy();
    expect(within(table).getAllByText('Needs account')).toHaveLength(1);
    // No download or upload before a file exists.
    expect(screen.queryByRole('button', { name: /^Download/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Upload checked file' })).toBeNull();

    await userEvent.click(screen.getByRole('button', { name: `Generate ${NAME}` }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByLabelText('Remarks (optional)'), 'first cut');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Generate file' }));
    await waitFor(() => expect(generated).toBe(1));
    const post = fetch.mock.calls.find((c) => (c[1] as RequestInit | undefined)?.method === 'POST');
    expect(String(post?.[0])).toBe(`${CV}/`);
    expect(sentBody(post)).toEqual({ remarks: 'first cut' });
    // The list is read again: the version now waits for its checked file.
    expect(await screen.findByRole('button', { name: 'Upload checked file' })).toBeTruthy();
  });

  it('generated with check rows: status, counts, Download, the upload form; Generate is a secondary new version', async () => {
    let downloads = 0;
    const fetch = routeFetch({
      '/credit-voucher/preview/': preview(),
      '/credit-voucher/': list([version()]),
      '/credit-voucher/11/': voucherDetail(),
      '/credit-voucher/11/download/': () => {
        downloads += 1;
        return new Response('Sr. No.,Account NBR\r\n', {
          status: 200,
          headers: { 'Content-Type': 'text/csv' },
        });
      },
    });
    vi.stubGlobal('fetch', fetch);
    renderPanel();

    const card = await screen.findByRole('group', { name: 'Rev credit file' });
    expect(within(card).getByText(NAME)).toBeTruthy();
    expect(within(card).getByText('Generated')).toBeTruthy();
    expect(within(card).getByText('2 row(s), Rs 500.00')).toBeTruthy();
    expect(within(card).getByText(/^1 row\(s\) need an account number/)).toBeTruthy();
    expect(within(card).getByText(/Not downloaded yet\./)).toBeTruthy();
    // The rows shown are the stored version's (detail), the check row highlighted.
    const table = await screen.findByRole('table', { name: `Rows of ${NAME}` });
    expect(within(table).getByText('Needs account')).toBeTruthy();
    expect(urlsOf(fetch)).toContain(`${CV}/11/`);
    expect(
      within(card).getByRole('button', { name: 'Generate a new version (upi rev 02112026.csv)' }),
    ).toBeTruthy();
    expect(within(card).getByRole('button', { name: 'Upload checked file' })).toHaveProperty(
      'disabled',
      true,
    );

    await userEvent.click(within(card).getByRole('button', { name: `Download ${NAME}` }));
    await waitFor(() => expect(downloads).toBe(1));
    expect(URL.createObjectURL).toHaveBeenCalled();
  });

  it('upload success: "N approved, M left open", the left-open cases, then the next step', async () => {
    let uploaded = 0;
    const fetch = routeFetch({
      '/credit-voucher/preview/': () =>
        json(200, uploaded ? preview({ rows: [ROWS[1]!], row_count: 1 }) : preview()),
      '/credit-voucher/': () => json(200, list([uploaded ? APPROVED : version()])),
      '/credit-voucher/11/': voucherDetail(),
      '/credit-voucher/11/upload/': () => {
        uploaded += 1;
        return json(200, {
          approved_count: 1,
          left_open_count: 1,
          approved_case_ids: [501],
          left_open: [{ case_id: 502, rrn: '530600000502', rule_code: 'R02', amount: '250.00' }],
          accounts_changed: 0,
          voucher: voucherDetail({ status: 'APPROVED' }),
        });
      },
    });
    vi.stubGlobal('fetch', fetch);
    renderPanel();

    const input = await screen.findByLabelText('Checked file (CSV)');
    expect(input.getAttribute('accept')).toContain('.csv');
    await userEvent.upload(input, new File(['a,b'], 'checked.csv', { type: 'text/csv' }));
    await userEvent.click(screen.getByRole('button', { name: 'Upload checked file' }));

    expect(await screen.findByText('1 approved, 1 left open')).toBeTruthy();
    const post = fetch.mock.calls.find((c) => String(c[0]).endsWith('/upload/'));
    expect(String(post?.[0])).toBe(`${CV}/11/upload/`);
    const body = (post?.[1] as RequestInit).body as FormData;
    expect((body.get('file') as File).name).toBe('checked.csv');
    const result = screen.getByRole('status');
    expect(within(result).getByRole('link', { name: '#502' }).getAttribute('href')).toBe('/upi/cases/502');
    expect(within(result).getByText(/then generate the SSKUPI file/)).toBeTruthy();
    // The list is read again: approved, no more upload form.
    await waitFor(() => expect(screen.queryByLabelText('Checked file (CSV)')).toBeNull());
  });

  it('upload refused (400): the per-row problems and the file problems, nothing approved', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/credit-voucher/preview/': preview(),
        '/credit-voucher/': list([version()]),
        '/credit-voucher/11/': voucherDetail(),
        '/credit-voucher/11/upload/': () =>
          json(400, {
            detail:
              'The checked file was refused; nothing was approved. Correct the rows listed and upload it again.',
            errors: {
              file: ['The debit row (500.00) must equal the sum of the credit rows (250.00).'],
              rows: [
                { line: 3, sr_no: '3', rrn: '530600000502', errors: ['The account number is missing.'] },
                {
                  line: 4,
                  sr_no: '4',
                  rrn: null,
                  errors: ["No RRN found in the narration (expected 'RRN/<rrn>').", 'Second problem.'],
                },
              ],
            },
          }),
      }),
    );
    renderPanel();

    await userEvent.upload(
      await screen.findByLabelText('Checked file (CSV)'),
      new File(['a,b'], 'checked.csv', { type: 'text/csv' }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Upload checked file' }));

    const alert = (await screen.findByText('The checked file was refused; nothing was approved')).closest(
      '[role="alert"]',
    ) as HTMLElement;
    expect(within(alert).getByText(/must equal the sum of the credit rows/)).toBeTruthy();
    const table = within(alert).getByRole('table', { name: 'Problems per row of the uploaded file' });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(3);
    expect(within(rows[1]!).getByText('530600000502')).toBeTruthy();
    expect(within(rows[1]!).getByText('The account number is missing.')).toBeTruthy();
    expect(within(rows[2]!).getByText('Second problem.')).toBeTruthy();
    // Nothing changed: the form is still there to try again.
    expect(screen.getByLabelText('Checked file (CSV)')).toBeTruthy();
  });

  it('approved: who approved it and the counts; no upload; "Approved" in the step', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/credit-voucher/preview/': preview({
          rows: [],
          row_count: 0,
          rows_to_check: 0,
          warnings: [],
          can_generate: false,
        }),
        '/credit-voucher/': list([
          APPROVED,
          version({
            id: 10,
            status: 'SUPERSEDED',
            status_label: 'Superseded by a later version',
            file_name: 'upi rev 02112026_0.csv',
            can_upload: false,
          }),
        ]),
      }),
    );
    renderPanel();

    const card = await screen.findByRole('group', { name: 'Rev credit file' });
    expect(within(card).getByText('Approved')).toBeTruthy();
    expect(within(card).getByText(/Approved by suco on .*: 1 approved, 1 left open\./)).toBeTruthy();
    expect(within(card).getByText('No case of this date is waiting for a CBS credit.')).toBeTruthy();
    expect(screen.queryByLabelText('Checked file (CSV)')).toBeNull();
    expect(screen.queryByRole('button', { name: /^Generate/ })).toBeNull();
    const versions = screen.getByRole('table', { name: 'Versions of the rev credit file', hidden: true });
    expect(within(versions).getByText('Superseded')).toBeTruthy();

    expect(revStep(list([APPROVED]), { row_count: 0 }, true)).toEqual({ state: 'done', badge: 'Approved' });
  });

  it('step tick', () => {
    expect(revStep(undefined, undefined, true)).toEqual({ state: 'waiting' });
    expect(revStep(list([version()]), { row_count: 2 }, true)).toEqual({
      state: 'attention',
      badge: 'Upload checked file',
    });
    expect(revStep(list([]), { row_count: 2 }, true)).toEqual({ state: 'attention', badge: 'To generate' });
    // Rows left open after an approval: a new version is due.
    expect(revStep(list([APPROVED]), { row_count: 1 }, true)).toEqual({
      state: 'attention',
      badge: 'To generate',
    });
    expect(revStep(list([]), { row_count: 0 }, true)).toEqual({ state: 'done', badge: 'Nothing to credit' });
    expect(revStep(list([]), { row_count: 0 }, false)).toEqual({ state: 'waiting' });
  });
});

describe('date page: rev step before the SSKUPI step', () => {
  function dateDetail(): BusinessDateDetail {
    return {
      ...makeDateOverview({ business_date: DATE, cases: { total: 3, active: 3 } }),
      checklist: [],
      unexpected_files: [],
      archived: false,
      runs: [],
      history: [],
    };
  }

  it('numbers the rev file 6 and SSKUPI 7; the SSKUPI step warns while a rev file waits for upload', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        [`/dates/${DATE}/`]: () => json(200, dateDetail()),
        '/results/summary/': () => json(404, {}),
        '/meta/': META,
        '/credit-voucher/preview/': preview(),
        '/credit-voucher/': list([version()]),
        '/credit-voucher/11/': voucherDetail(),
      }),
    );
    renderWithProviders(
      <MeProvider me={makeMe(['Operations User'])}>
        <Routes>
          <Route path="/:module/dates/:businessDate" element={<DateDetailPage />} />
        </Routes>
      </MeProvider>,
      { route: `/upi/dates/${DATE}` },
    );

    const rev = await screen.findByRole('region', { name: '6. Rev credit file' });
    const sskupi = screen.getByRole('region', { name: '7. SSKUPI file' });
    expect(rev.compareDocumentPosition(sskupi) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const nav = screen.getByRole('navigation', { name: 'Steps of the day' });
    expect(await within(nav).findByText('Upload checked file')).toBeTruthy();
    const hint = await within(sskupi).findByRole('note');
    expect(
      within(hint).getByText(/upi rev 02112026\.csv is generated but its checked file has not been uploaded/),
    ).toBeTruthy();
    expect(within(hint).getByRole('link', { name: 'step 6' }).getAttribute('href')).toBe('#step-rev');
  });
});
