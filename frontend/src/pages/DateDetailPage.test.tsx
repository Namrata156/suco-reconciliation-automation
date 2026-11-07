import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { BusinessDateDetail, RoleName } from '../api/types';
import { MeProvider } from '../auth/auth';
import {
  FILE_TYPES,
  json,
  makeDateOverview,
  makeFileRow,
  makeMe,
  makeRun,
  META,
  renderWithProviders,
  routeFetch,
} from '../test/fixtures';
import { DateDetailPage, reconciledLine } from './DateDetailPage';
import { dateIsBusy } from './DatesPage';
import { fileDateText, fileFiltersFromParams, fileStatusChoices, fileTypeChoices, shortStatus } from './FilesPage';


function detail(over: Partial<BusinessDateDetail> = {}): BusinessDateDetail {
  return {
    ...makeDateOverview({ cases: { total: 306, active: 306 }, matrix: { built: true, match_groups: 69_000, built_at: null } }),
    checklist: [],
    unexpected_files: [],
    archived: false,
    runs: [],
    history: [],
    ...over,
  };
}

function renderDate(roles: RoleName[]) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path="/:module/dates/:businessDate" element={<DateDetailPage />} />
      </Routes>
    </MeProvider>,
    { route: '/upi/dates/2026-07-14' },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Business-date overview (design 4.11)', () => {
  it('shows the counts, links the cases and starts a run for the Operations User', async () => {
    let runs = 0;
    const fetch = routeFetch({
      '/dates/2026-07-14/': () =>
        json(200, runs ? detail({ last_run: makeRun({ id: 31, status: 'QUEUED' }) }) : detail()),
      '/dates/2026-07-14/run/': () => {
        runs += 1;
        return json(202, makeRun({ id: 31, status: 'QUEUED' }));
      },
      '/results/summary/': () => json(404, { detail: 'No reconciliation results for this date.' }),
      '/meta/': META,
    });
    vi.stubGlobal('fetch', fetch);
    renderDate(['Operations User']);

    const cases = await screen.findByRole('link', { name: '306 cases: open the list' });
    expect(cases.getAttribute('href')).toBe('/upi/cases?business_date=2026-07-14&status=all');
    await userEvent.click(screen.getByRole('button', { name: 'Run reconciliation' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Start run' }));
    await waitFor(() => expect(runs).toBe(1));
    const post = fetch.mock.calls.find((c) => String(c[0]).endsWith('/run/'));
    expect(post?.[0]).toBe('/api/v1/upi/dates/2026-07-14/run/');
    expect((post?.[1] as RequestInit).method).toBe('POST');
    expect(await screen.findByText('Last run #31:')).toBeTruthy();
  });

  it('has no Run button for a Maker', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({ '/dates/2026-07-14/': () => json(200, detail()), '/results/summary/': () => json(404, {}) }),
    );
    renderDate(['Maker']);
    expect(await screen.findByText('An Operations User starts the run.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Run reconciliation/ })).toBeNull();
  });

  it('says when nothing exists for the date', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(404, { detail: 'No files or runs for this date.' })));
    renderDate(['Auditor']);
    expect(await screen.findByText('No files or runs for 14-Jul-2026.')).toBeTruthy();
    expect(screen.getByText('Nothing for this date')).toBeTruthy();
  });

  it('one plain reconciliation line; rule set, earlier runs and history collapsed (U1 3.2 / 3.3)', async () => {
    const d = detail({
      matrix: { built: true, match_groups: 69_120, built_at: '2026-09-26T05:00:00Z' },
      cases: { total: 21, active: 21 },
      rule_evaluation: {
        id: 5,
        rule_set: 'UPI rules v3.3',
        started_at: '2026-09-26T05:10:00Z',
        finished_at: '2026-09-26T05:12:00Z',
        group_count: 69_120,
        triggered_by: 'ops1',
      },
      runs: [makeRun({ id: 2 }), makeRun({ id: 1 })],
    });
    vi.stubGlobal(
      'fetch',
      routeFetch({ '/dates/2026-07-14/': () => json(200, d), '/results/summary/': () => json(404, {}) }),
    );
    renderDate(['Maker']);
    expect(
      await screen.findByText(/^Reconciled 26-Sep-2026 \d\d:\d\d IST - 69,120 transactions, 21 exceptions$/),
    ).toBeTruthy();
    expect(screen.queryByText('Presence matrix')).toBeNull();
    for (const name of ['Details', 'Earlier runs (2)', 'History']) {
      expect(screen.getByRole('heading', { name }).closest('details')!.open).toBe(false);
    }
    expect(screen.getByText(/^Rule set UPI rules v3\.3:/)).toBeTruthy();
    const files = screen.getByRole('link', { name: /files received: open the files list/ });
    expect(files.getAttribute('href')).toBe('/upi/files?business_date=2026-07-14');
  });

  it('says plainly when the date is not (fully) reconciled', () => {
    const o = makeDateOverview();
    expect(reconciledLine(o)).toBe('Not reconciled yet');
    expect(reconciledLine({ ...o, matrix: { built: true, match_groups: 5, built_at: null } })).toBe(
      'Transactions matched - 5 transactions; rules not applied yet',
    );
  });

  it('polls only while a run is active or files are processing', () => {
    expect(dateIsBusy(makeDateOverview())).toBe(false);
    expect(dateIsBusy(makeDateOverview({ last_run: makeRun({ status: 'RUNNING' }) }))).toBe(true);
    const o = makeDateOverview();
    expect(dateIsBusy({ ...o, files: { ...o.files, files_processing: 2 } })).toBe(true);
  });
});

describe('files list helpers (design 4.9)', () => {
  it('reads the filters from the address', () => {
    const f = fileFiltersFromParams(new URLSearchParams('business_date=2026-07-14&status=LOADED&page=2&x=1'));
    expect(f).toEqual({ business_date: '2026-07-14', status: 'LOADED', page: 2, page_size: 50 });
  });

  it('takes the statuses from meta, in pipeline order, with short labels (Slice 3)', () => {
    expect(fileStatusChoices(META).map((s) => s.label)).toEqual([
      'Received',
      'Staged',
      'Awaiting business date selection',
      'Loaded',
      'Validation failed',
      'Failed',
    ]);
    expect(fileStatusChoices(undefined)).toEqual([]);
    expect(shortStatus('Staged (raw rows written to the per-file-type staging table)')).toBe('Staged');
  });

  it('offers one file-type choice per config, so ACQ and ISS are apart (?file_type_config=<id>)', () => {
    const choices = fileTypeChoices({ ...META, file_type_configs: FILE_TYPES });
    expect(choices.filter((c) => c.label.startsWith('Raw Data'))).toEqual([
      { value: '3', label: 'Raw Data ACQ' },
      { value: '4', label: 'Raw Data ISS' },
    ]);
  });

  it('reads the file-type config filter from the address', () => {
    const f = fileFiltersFromParams(new URLSearchParams('file_type_config=3'));
    expect(f.file_type_config).toBe('3');
  });

  it('shows the batch date for a CBS file still waiting for its date', () => {
    expect(fileDateText(makeFileRow())).toBe('14-Jul-2026');
    expect(fileDateText(makeFileRow({ business_date: null }))).toBe('not chosen yet (uploaded for 14-Jul-2026)');
  });
});
