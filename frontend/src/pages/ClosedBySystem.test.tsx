/**
 * Owner decision 2026-11-09: R01 / R03 / R04a cases close by themselves when
 * their no-money reply goes into a generated SSKUPI file (COMPLETED with a
 * CLOSED_BY_SYSTEM event, no actor). The queue and the case page say so.
 */
import { screen, within } from '@testing-library/react';
import type { ReactElement } from 'react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CaseDetail } from '../api/types';
import { MeProvider } from '../auth/auth';
import { makeCase, makeMe, META, renderWithProviders, routeFetch } from '../test/fixtures';
import { CaseDetailPage } from './CaseDetailPage';
import { CaseQueuePage } from './CaseQueuePage';

afterEach(() => {
  vi.unstubAllGlobals();
});

const FILE = 'SSKUPI14072026.csv';
const META_EV = {
  ...META,
  event_types: [
    { value: 'CREATED', label: 'Created' },
    { value: 'CLOSED_BY_SYSTEM', label: 'Closed by the system (no-money reply included in an NPCI file)' },
  ],
};

function render(path: string, route: string, element: ReactElement) {
  return renderWithProviders(
    <MeProvider me={makeMe(['Maker'])}>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

const closedCase = (): CaseDetail => ({
  ...makeCase({ id: 310, rrn: '419500000310', status: 'COMPLETED', closed_by_system: true }),
  npci_file: { id: 7, file_name: FILE, file_date: '2026-07-14' },
  default_response_code: 'DRC 102 - Reversed online',
  needs_review: false,
  needs_review_reason: '',
  multiple_adjustment_rows: false,
  no_deadline: false,
  result_missing: false,
  created_at: '2026-07-15T05:30:00Z',
  updated_at: '2026-07-15T06:00:00Z',
  rule_result: null,
  warnings: [],
  overlay_rows: [],
  settlement: null,
  actions: [
    {
      id: 41,
      rule_result: null,
      rule_code: 'R01',
      action_type: 'DRC_102',
      response_code: 'DRC 102 - Reversed online',
      remarks: '',
      evidence_cbs_reversal_date: null,
      evidence_cbs_reversal_amount: null,
      evidence_cbs_reversal_rrn: '',
      evidence_notes: '',
      submitted_by: 'maker1',
      submitted_at: '2026-07-15T05:40:00Z',
      decision: 'WITHDRAWN',
      decided_by: null,
      decided_at: '2026-07-15T06:00:00Z',
      decision_remarks: 'Withdrawn: Closed by the system',
      self_approved: false,
      external_reference: '',
      completed_at: null,
    },
  ],
  events: [
    {
      id: 900,
      created_at: '2026-07-15T06:00:00Z',
      event_type: 'CLOSED_BY_SYSTEM',
      actor_username: '',
      source: 'system: NPCI file generation',
      old_status: 'PENDING_APPROVAL',
      new_status: 'COMPLETED',
      remarks: `Closed by the system: the no-money reply was included in NPCI file ${FILE} (file id 7).`,
      changes: { closed_by: [null, 'system'], npci_file_name: [null, FILE] },
      action_snapshot: null,
    },
  ],
  available_transitions: [],
  what_happened: null,
});

describe('Closed by system (owner 2026-11-09)', () => {
  it('the case queue shows the badge on a system-closed case only', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/cases/': {
          count: 2,
          next: null,
          previous: null,
          results: [
            makeCase({ rrn: '419500000310', status: 'COMPLETED', closed_by_system: true }),
            makeCase({ rrn: '419500000311', status: 'COMPLETED', closed_by_system: false }),
          ],
        },
        '/meta/': META,
      }),
    );
    render('/:module/cases', '/upi/cases?status=all', <CaseQueuePage />);
    const table = await screen.findByRole('table', { name: 'Exception cases' });
    const rowOf = async (rrn: string) => (await within(table).findByRole('link', { name: rrn })).closest('tr')!;
    expect(within(await rowOf('419500000310')).getByText('Closed by system')).toBeTruthy();
    expect(within(await rowOf('419500000311')).queryByText('Closed by system')).toBeNull();
  });

  it('the case page names the file, the history reads "System", the withdrawn action says by whom', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/cases/310/': closedCase(), '/meta/': META_EV }));
    render('/:module/cases/:caseId', '/upi/cases/310', <CaseDetailPage />);

    expect(await screen.findByText('Closed by system')).toBeTruthy();
    const note = screen.getByRole('note', { name: 'Closed by system' });
    expect(within(note).getByRole('link', { name: FILE }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-14#step-sskupi',
    );
    expect(note.textContent).toContain('no Maker or Checker step');

    const history = screen.getByRole('table', { name: 'Case history' });
    const row = within(history).getAllByRole('row')[1]!;
    expect(within(row).getByText('Closed by the system (no-money reply included in an NPCI file)')).toBeTruthy();
    expect(within(row).getByText('System')).toBeTruthy();
    expect(within(row).getByText('Pending approval -> Completed')).toBeTruthy();

    expect(screen.getByText(/^Withdrawn by the system on/)).toBeTruthy();
    expect(screen.getByText('No action is open to you on this case now.')).toBeTruthy();
  });
});
