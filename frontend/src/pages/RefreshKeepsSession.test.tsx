/**
 * "Refresh" while automatic refresh is paused for inactivity (lib/polling.ts):
 * the press must still reach the server, which renews the 15-minute session
 * (SESSION_SAVE_EVERY_REQUEST), and move the SPA's session clock, so a user
 * (or the e2e spec) pressing Refresh stays signed in during a long run.
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { sessionClock } from '../api/client';
import { MeProvider } from '../auth/auth';
import { makeDateOverview, makeFileRow, makeMe, makeRun, renderWithProviders } from '../test/fixtures';
import { DateDetailPage } from './DateDetailPage';
import { FilesPage } from './FilesPage';

// The user has been idle: automatic refresh is paused on every screen.
vi.mock('../lib/polling', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/polling')>()),
  useUserActive: () => false,
}));

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });

afterEach(() => {
  vi.unstubAllGlobals();
});

function render(path: string, route: string, element: ReactElement) {
  return renderWithProviders(
    <MeProvider me={makeMe(['Operations User'])}>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

describe('Refresh keeps the session alive while auto-refresh is paused', () => {
  it('date overview with a run in progress', async () => {
    const running = {
      ...makeDateOverview({ last_run: makeRun({ status: 'RUNNING', finished_at: null }) }),
      checklist: [],
      unexpected_files: [],
      archived: false,
      runs: [],
      history: [],
    };
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(json(running)));
    vi.stubGlobal('fetch', fetch);
    render('/:module/dates/:businessDate', '/upi/dates/2026-07-14', <DateDetailPage />);

    expect(await screen.findByText(/Automatic refresh paused while you are inactive/)).toBeTruthy();
    expect(fetch).toHaveBeenCalledTimes(1);
    sessionClock.touch(0);

    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    expect(fetch.mock.calls[1]?.[0]).toBe('/api/v1/upi/dates/2026-07-14/');
    await waitFor(() => expect(sessionClock.lastRequestAt()).toBeGreaterThan(0));
    // Exactly one control is named "Refresh" (the e2e spec clicks it in strict mode).
    expect(screen.getAllByRole('button', { name: 'Refresh' })).toHaveLength(1);
  });

  it('files list with files still processing', async () => {
    const page = { count: 1, next: null, previous: null, results: [makeFileRow({ state: 'PROCESSING' })], processing_count: 1 };
    const fetch = vi.fn().mockImplementation(() => Promise.resolve(json(page)));
    vi.stubGlobal('fetch', fetch);
    render('/:module/files', '/upi/files', <FilesPage />);

    expect(await screen.findByText(/Automatic refresh paused while you are inactive/)).toBeTruthy();
    const calls = fetch.mock.calls.length;
    sessionClock.touch(0);
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(fetch.mock.calls.length).toBe(calls + 1));
    expect(String(fetch.mock.calls[calls]?.[0])).toMatch(/^\/api\/v1\/upi\/files\//);
    await waitFor(() => expect(sessionClock.lastRequestAt()).toBeGreaterThan(0));
  });
});
