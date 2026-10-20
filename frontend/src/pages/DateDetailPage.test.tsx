import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { BusinessDateDetail, RoleName } from '../api/types';
import { MeProvider } from '../auth/auth';
import { makeDateOverview, makeFileRow, makeMe, makeRun, renderWithProviders } from '../test/fixtures';
import { DateDetailPage } from './DateDetailPage';
import { dateIsBusy } from './DatesPage';
import { fileDateText, fileFiltersFromParams, FILE_STATUSES, shortStatus } from './FilesPage';

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

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
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(200, detail()))
      .mockResolvedValueOnce(json(202, makeRun({ id: 31, status: 'QUEUED' })))
      .mockResolvedValue(json(200, detail({ last_run: makeRun({ id: 31, status: 'QUEUED' }) })));
    vi.stubGlobal('fetch', fetch);
    renderDate(['Operations User']);

    const cases = await screen.findByRole('link', { name: '306 cases: open the list' });
    expect(cases.getAttribute('href')).toBe('/upi/cases?business_date=2026-07-14&status=all');
    await userEvent.click(screen.getByRole('button', { name: 'Run reconciliation' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Start run' }));
    await waitFor(() => expect(fetch.mock.calls[1]?.[0]).toBe('/api/v1/upi/dates/2026-07-14/run/'));
    expect((fetch.mock.calls[1]?.[1] as RequestInit).method).toBe('POST');
    expect(await screen.findByText('Last run #31:')).toBeTruthy();
  });

  it('has no Run button for a Maker', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(200, detail())));
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

  it('lists the 9 processing statuses (meta gap) and shortens labels', () => {
    expect(FILE_STATUSES).toHaveLength(9);
    expect(shortStatus('Staged (raw rows written to the per-file-type staging table)')).toBe('Staged');
  });

  it('shows the batch date for a CBS file still waiting for its date', () => {
    expect(fileDateText(makeFileRow())).toBe('14-Jul-2026');
    expect(fileDateText(makeFileRow({ business_date: null }))).toBe('not chosen yet (uploaded for 14-Jul-2026)');
  });
});
