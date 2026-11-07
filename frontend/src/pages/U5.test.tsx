/**
 * UI refinements U5 (docs/ui_refinements_api_contract_u4u5.txt): the NPCI
 * files report (8.1, F1) and the dashboard (8.3, F2): tables and the four charts.
 */
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Dashboard, DashboardDate, NpciFilesReport, NpciFilesReportRow, RoleName } from '../api/types';
import { MeProvider } from '../auth/auth';
import {
  exceptionRows,
  npciRows,
  replyTypesPresent,
  settlementRows,
  volumeRows,
} from '../features/dashboard/DashboardCharts';
import { makeMe, renderWithProviders, routeFetch } from '../test/fixtures';
import { DashboardPage } from './DashboardPage';
import { NpciFilesReportPage } from './NpciFilesReportPage';

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderAt(path: string, route: string, element: ReactElement, roles: RoleName[] = ['Auditor']) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

const fileRow = (o: Partial<NpciFilesReportRow> = {}): NpciFilesReportRow => ({
  id: 7,
  file_date: '2026-07-14',
  business_date: '2026-07-14',
  sequence: 1,
  file_name: 'SSKUPI14072026.csv',
  file_format: 'SSKUPI',
  row_count: 12,
  total_amount: '1500.00',
  partial: false,
  not_included_count: 0,
  sha256: 'ab'.repeat(32),
  size_bytes: 900,
  generated_by: 'chk1',
  generated_at: '2026-07-15T06:00:00Z',
  remarks: '',
  download_url: null,
  downloads: [],
  download_count: 0,
  links: { file: '/upi/npci-files/7', date: '/upi/dates/2026-07-14' },
  ...o,
});

const report = (rows: NpciFilesReportRow[]): NpciFilesReport => ({
  count: rows.length,
  next: null,
  previous: null,
  results: rows,
  totals: { files: 9, rows: 412, amount: '123456.50', downloaded: 8, not_downloaded: 1 },
  report: {
    key: 'npci_files',
    title: 'NPCI files (SSKUPI)',
    module: 'upi',
    filters: { date_from: '2026-07-01', date_to: '2026-07-31', downloaded: null, ordering: '-business_date' },
    generated_at: '2026-07-31T10:00:00Z',
    generated_by: 'aud1',
  },
  export: null,
});

describe('NPCI files report (8.1)', () => {
  it('totals for the range, one row per file, Download only where the server gives a link', async () => {
    const fetch = routeFetch({
      '/reports/npci-files/': report([
        fileRow(),
        fileRow({
          id: 8,
          sequence: 2,
          file_name: 'SSKUPI14072026_2.csv',
          download_url: '/api/v1/upi/npci-files/8/download/',
          downloads: [{ by: 'ops1', at: '2026-07-15T07:00:00Z' }],
          download_count: 1,
        }),
      ]),
    });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/reports/npci-files',
      '/upi/reports/npci-files?date_from=2026-07-01&date_to=2026-07-31',
      <NpciFilesReportPage />,
    );
    const totals = await screen.findByRole('group', { name: 'Totals for the range' });
    expect(totals.textContent).toBe(
      '9 file(s), 412 row(s), Rs 1,23,456.50. 8 downloaded, 1 never downloaded.',
    );
    const table = screen.getByRole('table', { name: 'NPCI files (SSKUPI)' });
    expect(within(table).getByText('Never downloaded')).toBeTruthy();
    expect(within(table).getByText('Once')).toBeTruthy();
    expect(within(table).queryByRole('button', { name: 'Download SSKUPI14072026.csv' })).toBeNull();
    expect(within(table).getByRole('button', { name: 'Download SSKUPI14072026_2.csv' })).toBeTruthy();
    expect(within(table).getAllByRole('link', { name: '14-Jul-2026' })[0]?.getAttribute('href')).toBe(
      '/upi/dates/2026-07-14#step-sskupi',
    );
    // No Excel button until the shared export exists ("export": null).
    expect(screen.queryByRole('button', { name: /Excel|Download report/ })).toBeNull();
    const url = String(fetch.mock.calls[0]?.[0]);
    expect(url).toContain('date_from=2026-07-01');
    expect(url).toContain('date_to=2026-07-31');
  });

  it('the "Downloaded" filter is sent to the server', async () => {
    const fetch = routeFetch({ '/reports/npci-files/': report([]) });
    vi.stubGlobal('fetch', fetch);
    renderAt(
      '/:module/reports/npci-files',
      '/upi/reports/npci-files?date_from=2026-07-01&date_to=2026-07-31&downloaded=false',
      <NpciFilesReportPage />,
    );
    expect(await screen.findByText('No SSKUPI file was generated for these business dates.')).toBeTruthy();
    expect(String(fetch.mock.calls[0]?.[0])).toContain('downloaded=false');
  });
});

const dashboard: Dashboard = {
  range: { date_from: '2026-06-15', date_to: '2026-07-14', dates: 1 },
  reply_types: [
    { value: 'DRC', label: 'DRC' },
    { value: 'NEEDS_REVIEW', label: 'Needs review' },
  ],
  dates: [
    {
      business_date: '2026-07-14',
      status: 'FILE_PENDING',
      status_label: 'SSKUPI file to generate / download',
      volume: {
        transactions: 70210,
        npci: { count: 70500, amount: '98765432.10' },
        cbs: { count: 69900, amount: '97654321.00' },
      },
      exceptions: { DRC: 179, NEEDS_REVIEW: 606, total: 785 },
      open_cases: 12,
      settlement: { status: 'UNEXPLAINED', tallied: false, unexplained_amount: '200.00' },
      sskupi: { state: 'GENERATED', file_count: 1, not_downloaded: 1, pending: true },
      link: '/upi/dates/2026-07-14',
    },
  ],
  open_cases_by_age: {
    buckets: [{ label: '0-1 d', min_days: 0, max_days: 1, count: 40, overdue: 3 }],
    total: 95,
    overdue: 11,
  },
  generated_at: '2026-07-15T10:00:00Z',
};

describe('Dashboard tables (8.3)', () => {
  it('no dates chosen: asks for the last 30 dates; per-date table and open cases by age', async () => {
    const fetch = routeFetch({ '/dashboard/': dashboard });
    vi.stubGlobal('fetch', fetch);
    renderAt('/:module/dashboard', '/upi/dashboard', <DashboardPage />);
    const perDate = await screen.findByRole('table', { name: 'Per business date' });
    expect(String(fetch.mock.calls[0]?.[0])).toBe('/api/v1/upi/dashboard/');
    expect(within(perDate).getByText('SSKUPI file to generate / download')).toBeTruthy();
    expect(within(perDate).getByText('DRC 179, Needs review 606')).toBeTruthy();
    expect(within(perDate).getByText('1 not downloaded')).toBeTruthy();
    const age = screen.getByRole('table', { name: 'Open cases by age' });
    expect(within(age).getByText('0-1 d')).toBeTruthy();
    expect(within(age).getByText('95')).toBeTruthy();
    expect(screen.getByText(/the last 30; choose dates above/)).toBeTruthy();
  });

  it('the charts sit above the per-date table', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/dashboard/': dashboard }));
    renderAt('/:module/dashboard', '/upi/dashboard', <DashboardPage />);
    const perDate = await screen.findByRole('table', { name: 'Per business date' });
    const charts = screen.getByRole('heading', { name: 'Charts per business date' });
    expect(charts.compareDocumentPosition(perDate) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('a chosen range is sent', async () => {
    const fetch = routeFetch({ '/dashboard/': dashboard });
    vi.stubGlobal('fetch', fetch);
    renderAt('/:module/dashboard', '/upi/dashboard', <DashboardPage />);
    await screen.findByRole('table', { name: 'Per business date' });
    await userEvent.click(screen.getByRole('button', { name: 'Last month' }));
    await screen.findByText(/business date\(s\) with files or runs/);
    expect(fetch.mock.calls.some((c) => String(c[0]).includes('date_from='))).toBe(true);
  });
});

const day = (business_date: string, o: Partial<DashboardDate> = {}): DashboardDate => ({
  ...(dashboard.dates[0] as DashboardDate),
  business_date,
  link: `/upi/dates/${business_date}`,
  ...o,
});

const twoDays: Dashboard = {
  ...dashboard,
  range: { date_from: '2026-07-13', date_to: '2026-07-14', dates: 2 },
  reply_types: [
    { value: 'DRC', label: 'DRC' },
    { value: 'TCC', label: 'TCC' },
    { value: 'RET', label: 'RET' },
    { value: 'NEEDS_REVIEW', label: 'Needs review' },
  ],
  dates: [
    day('2026-07-13', {
      volume: {
        transactions: 1000,
        npci: { count: 1010, amount: '500.00' },
        cbs: { count: 990, amount: '490.00' },
      },
      exceptions: { DRC: 2, RET: 1, total: 3 },
      settlement: { status: 'TALLIED', tallied: true, unexplained_amount: null },
      sskupi: { state: 'DOWNLOADED', file_count: 2, not_downloaded: 0, pending: false },
    }),
    day('2026-07-14', {
      volume: {
        transactions: 70210,
        npci: { count: 70500, amount: '98765432.10' },
        cbs: { count: 69900, amount: '97654321.00' },
      },
      exceptions: { DRC: 179, NEEDS_REVIEW: 606, total: 785 },
      settlement: null,
      sskupi: { state: 'GENERATED', file_count: 1, not_downloaded: 1, pending: true },
    }),
  ],
};

const noActivity: Dashboard = {
  ...twoDays,
  dates: twoDays.dates.map((d) => ({
    ...d,
    volume: { transactions: 0, npci: { count: 0, amount: '0.00' }, cbs: { count: 0, amount: '0.00' } },
    exceptions: { total: 0 },
    settlement: { status: 'UNEXPLAINED', tallied: false, unexplained_amount: '200.00' },
    sskupi: { state: 'NONE', file_count: 0, not_downloaded: 0, pending: false },
  })),
};

const figure = (name: string) => screen.getByRole('figure', { name });

describe('Dashboard charts (8.3, owner decisions 2026-11-07)', () => {
  it('exactly four charts with their summaries when there is data', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/dashboard/': twoDays }));
    renderAt('/:module/dashboard', '/upi/dashboard', <DashboardPage />);
    await screen.findByRole('heading', { name: 'Charts per business date' });
    expect(screen.getAllByRole('figure')).toHaveLength(4);
    expect(
      within(figure('Settlement: tallied / not tallied')).getByText(
        'Tallied on 1 of 2 date(s), not tallied on 0, no settlement result yet on 1.',
      ),
    ).toBeTruthy();
    expect(
      within(figure('Exceptions by reply type')).getByText(
        '788 exception(s): DRC 181, RET 1, Needs review 606.',
      ),
    ).toBeTruthy();
    expect(
      within(figure('Transaction volume')).getByText(
        '71,210 transaction(s) on 2 date(s); highest 70,210 on 14-Jul-2026.',
      ),
    ).toBeTruthy();
    expect(
      within(figure('NPCI files (SSKUPI) generated / downloaded')).getByText(
        '3 file(s) generated, 2 downloaded, 1 not downloaded.',
      ),
    ).toBeTruthy();
    // A chart is drawn in each card (recharts' container is rendered).
    for (const f of screen.getAllByRole('figure')) {
      expect(f.querySelector('.recharts-responsive-container')).not.toBeNull();
    }
    // Open cases by age stays a table, not a chart.
    expect(screen.getByRole('table', { name: 'Open cases by age' })).toBeTruthy();
  });

  it('no dates: one empty message and no chart; the table says the same', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({ '/dashboard/': { ...dashboard, dates: [], range: { ...dashboard.range, dates: 0 } } }),
    );
    renderAt('/:module/dashboard', '/upi/dashboard', <DashboardPage />);
    expect(
      await screen.findByText(
        'No business date with files or runs in this range, so there is nothing to chart.',
      ),
    ).toBeTruthy();
    expect(screen.queryAllByRole('figure')).toHaveLength(0);
    expect(document.querySelector('.recharts-responsive-container')).toBeNull();
    expect(screen.getByText('No business date with files or runs in this range.')).toBeTruthy();
  });

  it('dates without exceptions, volume or files: those charts show a sentence instead', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/dashboard/': noActivity }));
    renderAt('/:module/dashboard', '/upi/dashboard', <DashboardPage />);
    await screen.findByRole('heading', { name: 'Charts per business date' });
    expect(
      within(figure('Exceptions by reply type')).getByText('No exceptions on these dates.'),
    ).toBeTruthy();
    expect(within(figure('Transaction volume')).getByText('No transactions on these dates.')).toBeTruthy();
    expect(
      within(figure('NPCI files (SSKUPI) generated / downloaded')).getByText(
        'No SSKUPI file was generated for these dates.',
      ),
    ).toBeTruthy();
    for (const name of [
      'Exceptions by reply type',
      'Transaction volume',
      'NPCI files (SSKUPI) generated / downloaded',
    ]) {
      expect(figure(name).querySelector('.recharts-responsive-container')).toBeNull();
    }
    // Settlement still has something to show: both dates not tallied.
    const settlement = figure('Settlement: tallied / not tallied');
    expect(within(settlement).getByText('Tallied on 0 of 2 date(s), not tallied on 2.')).toBeTruthy();
    expect(settlement.querySelector('.recharts-responsive-container')).not.toBeNull();
  });
});

describe('Dashboard chart data (8.3)', () => {
  it('one row per date, oldest first, in the shape each chart stacks', () => {
    expect(
      settlementRows(twoDays.dates).map(({ day: d, tallied, not_tallied, no_result }) => [
        d,
        tallied,
        not_tallied,
        no_result,
      ]),
    ).toEqual([
      ['13-Jul', 1, 0, 0],
      ['14-Jul', 0, 0, 1],
    ]);
    // Only reply types that occur in the range become series (TCC does not).
    expect(replyTypesPresent(twoDays).map((t) => t.value)).toEqual(['DRC', 'RET', 'NEEDS_REVIEW']);
    expect(exceptionRows(twoDays).map(({ DRC, RET, NEEDS_REVIEW }) => [DRC, RET, NEEDS_REVIEW])).toEqual([
      [2, 1, 0],
      [179, 0, 606],
    ]);
    expect(volumeRows(twoDays.dates).map((r) => r.transactions)).toEqual([1000, 70210]);
    expect(
      npciRows(twoDays.dates).map(({ downloaded, not_downloaded }) => [downloaded, not_downloaded]),
    ).toEqual([
      [2, 0],
      [0, 1],
    ]);
  });
});
