/**
 * UI refinements U2 / U3 (docs/ui_refinements_api_contract.txt): the day's
 * steps on Home and the date page, the queue's reply tabs, the Approvals
 * inbox, the case buttons, response codes by reply type, Find an RRN, the
 * original file, the settlement sentence and the audit timeline.
 */
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type {
  CaseReply,
  DayBlock,
  ResponseCodeRow,
  RoleName,
  RrnLookup,
  SettlementSummary,
  SskupiFull,
} from '../api/types';
import { MeProvider } from '../auth/auth';
import { allEventTypes } from '../features/audit/AuditLists';
import { jobSteps, sskupiStep, TodaysJob } from '../features/home/TodaysJob';
import { nothingNewLine } from '../features/npci/NpciFilePanel';
import { replyGroups } from '../features/settings/ResponseCodesTab';
import { noteText, settlementSentence } from '../features/settlement/SettlementSummaryView';
import {
  makeCase,
  makeDateOverview,
  makeFileRow,
  makeMe,
  META,
  renderWithProviders,
  routeFetch,
} from '../test/fixtures';
import { ApprovalsPage } from './PendingApprovalsPage';
import { transitionLabel } from './CaseDetailPage';
import { CaseQueuePage, orderingChoices } from './CaseQueuePage';
import { dateSteps } from './DateDetailPage';
import { FileDetailPage } from './FileDetailPage';
import { RrnLookupPage } from './RrnLookupPage';

afterEach(() => {
  vi.unstubAllGlobals();
});

const page = <T,>(results: T[], extra: object = {}) => ({
  count: results.length,
  next: null,
  previous: null,
  results,
  ...extra,
});

function renderAt(path: string, route: string, element: ReactElement, roles: RoleName[] = ['Checker']) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path={path} element={element} />
        <Route path="/:module/dates/:businessDate/groups/:groupId" element={<p>Transaction page</p>} />
        <Route path="/:module/approvals" element={<p>Approvals inbox</p>} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

// ---------------------------------------------------------------------------
// fixtures (made-up values)
// ---------------------------------------------------------------------------
const sskupi = (o: Partial<SskupiFull> = {}): SskupiFull => ({
  state: 'GENERATED',
  state_label: 'Generated, not downloaded',
  file_count: 1,
  latest: {
    id: 7,
    file_name: 'SSKUPI14072026.csv',
    sequence: 1,
    row_count: 12,
    generated_at: '2026-07-15T06:00:00Z',
    generated_by: 'chk1',
    download_count: 0,
    last_download: null,
  },
  files: [],
  waiting_rows: 0,
  next_file_name: 'SSKUPI14072026_2.csv',
  held_for_checker: 1,
  left_out: 0,
  changed_since_included: 0,
  can_generate: false,
  not_downloaded: ['SSKUPI14072026.csv'],
  ...o,
});

const day = (o: Partial<DayBlock> = {}): DayBlock => ({
  business_date: '2026-07-14',
  status: 'FILE_PENDING',
  status_label: 'SSKUPI file to generate / download',
  status_detail: 'All cases worked. Download SSKUPI14072026.csv.',
  files: {
    expected: 60,
    present: 60,
    missing: 0,
    loaded: 60,
    processing: 0,
    failed: 0,
    awaiting_date_selection: 0,
  },
  run: { state: 'DONE', run_id: 41, requested_by: 'ops1', finished_at: '2026-07-15T05:30:00Z', error: '' },
  results: {
    reconciled_at: '2026-07-15T05:30:00Z',
    rule_set: 'v3.3',
    transactions: 70210,
    matched: 69020,
    exceptions: 1003,
  },
  settlement: { status: 'TALLIED', status_label: 'Tallied', unexplained_amount: '0.00' },
  cases: { open: 0, total: 1004 },
  sskupi: sskupi(),
  link: '/upi/dates/2026-07-14',
  ...o,
});

const reply = (o: Partial<CaseReply> = {}): CaseReply => ({
  reply_type: 'DRC',
  reply_type_label: 'DRC',
  action_type: 'DRC_102',
  action_type_label: 'DRC reply 102',
  flag: 'DRC',
  code: '102',
  text: 'Online Reversal',
  label: 'DRC 102 - Online Reversal',
  source: 'RULE',
  source_label: "The rule's suggested reply",
  ...o,
});

// ---------------------------------------------------------------------------
describe("Home: today's job and the SSKUPI tile (1.1 / 1.2)", () => {
  it('four steps; a file never downloaded is the thing to do', () => {
    const steps = jobSteps(day());
    expect(steps.map((s) => [s.title, s.state])).toEqual([
      ['Files', 'done'],
      ['Run', 'done'],
      ['Cases', 'done'],
      ['SSKUPI file', 'attention'],
    ]);
    expect(steps[3]?.badge).toBe('Download SSKUPI14072026.csv');
    expect(sskupiStep(sskupi({ state: 'DOWNLOADED', not_downloaded: [] }), true).state).toBe('done');
    expect(
      sskupiStep(sskupi({ state: 'NONE', latest: null, not_downloaded: [], waiting_rows: 4 }), true),
    ).toEqual({
      state: 'attention',
      badge: 'To generate',
    });
  });

  it("shows the server's status sentence (FILE_PENDING) and a Download call to action", () => {
    renderWithProviders(<TodaysJob module="upi" day={day()} />);
    expect(screen.getByText('SSKUPI file to generate / download')).toBeTruthy();
    expect(screen.getByText('All cases worked. Download SSKUPI14072026.csv.')).toBeTruthy();
    expect(screen.getByRole('link', { name: /^Step 4, SSKUPI file/ }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-14#step-sskupi',
    );
    expect(screen.getByRole('link', { name: 'Download SSKUPI14072026.csv' }).getAttribute('href')).toBe(
      '/upi/dates/2026-07-14#step-sskupi',
    );
  });

  it('a status the SPA does not know yet still reads correctly', () => {
    renderWithProviders(
      <TodaysJob
        module="upi"
        day={day({ status: 'SOMETHING_NEW', status_label: 'Something new', sskupi: null })}
      />,
    );
    expect(screen.getByText('Something new')).toBeTruthy();
    expect(screen.queryByText('SSKUPI file')).toBeNull();
  });
});

describe('Date page steps (3.1)', () => {
  it('seven steps in order with their ticks', () => {
    const d = makeDateOverview({
      files: { ...makeDateOverview().files, missing: 0, loaded: 66 },
      rule_evaluation: {
        id: 5,
        rule_set: 'v3.3',
        started_at: '2026-07-15T05:10:00Z',
        finished_at: '2026-07-15T05:12:00Z',
        group_count: 100,
        triggered_by: 'ops1',
      },
      cases: { total: 21, active: 3 },
      settlement: { id: 8, status: 'UNEXPLAINED', reasons: [], computed_at: '', unexplained_amount: '10.00' },
      status: 'CASES_OPEN',
      sskupi: { state: 'NONE', state_label: 'No file yet', file_count: 0, latest: null, not_downloaded: [] },
    });
    expect(
      dateSteps(d).map((s) => `${s.n}. ${s.title}: ${s.state}${s.badge ? ` (${s.badge})` : ''}`),
    ).toEqual([
      '1. Files arrived: done',
      '2. Run reconciliation: done',
      '3. Results: done',
      '4. Settlement tally: attention (Not tallied)',
      '5. Cases to work: attention (3 open)',
      '6. Rev credit file: waiting',
      '7. SSKUPI file: waiting (No file yet)',
    ]);
  });

  it('files changed since the run: run again', () => {
    const d = makeDateOverview({
      status: 'READY_TO_RUN',
      rule_evaluation: {
        id: 5,
        rule_set: 'v3.3',
        started_at: '2026-07-15T05:10:00Z',
        finished_at: '2026-07-15T05:12:00Z',
        group_count: 100,
        triggered_by: 'ops1',
      },
    });
    expect(dateSteps(d)[1]).toMatchObject({ state: 'attention', badge: 'Run again' });
  });
});

describe('Case queue: reply tabs, few filters (6.1 / 6.2)', () => {
  it('tabs carry their counts; a tab sets reply_type; the rest of the filters are under "More filters"', async () => {
    const fetch = routeFetch({
      '/cases/': page(
        [
          makeCase({
            id: 4,
            amount: '140.50',
            rule_name: 'DRC - debited and already reversed',
            reply: reply(),
          }),
        ],
        { reply_type_counts: { DRC: 179, TCC: 2, RET: 18, RRC: 54, NEEDS_REVIEW: 607, SETTLEMENT: 1 } },
      ),
      '/meta/': META,
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/:module/cases', '/upi/cases', <CaseQueuePage />, ['Maker']);
    expect(await screen.findByRole('tab', { name: 'DRC (179)' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Needs review (607)' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'All (861)' })).toBeTruthy();
    const table = screen.getByRole('table', { name: 'Exception cases' });
    expect(await within(table).findByText('DRC 102 - Online Reversal')).toBeTruthy();
    expect(within(table).getByText('Rs 140.50')).toBeTruthy();
    expect(within(table).getByText('DRC - debited and already reversed')).toBeTruthy();
    // Status, rule ... wait under "More filters", closed while none is set.
    expect(screen.getByText('More filters').closest('details')!.open).toBe(false);
    // The case priority is not shown: no column, no filter, no sort.
    expect(within(table).queryByRole('columnheader', { name: 'Priority' })).toBeNull();
    expect(screen.queryByLabelText('Priority')).toBeNull();
    expect(screen.getAllByLabelText('Status').length).toBeGreaterThan(0); // More filters are rendered, only closed
    expect(orderingChoices(['priority', 'due_date']).map((o) => o.value)).toEqual(['due_date', '-due_date']);
    // Assigned to me is a filter, no "Awaiting my approval" (that is the Approvals inbox).
    expect(screen.getByRole('radio', { name: 'Assigned to me' })).toBeTruthy();
    expect(screen.queryByRole('radio', { name: 'Awaiting my approval' })).toBeNull();

    await userEvent.click(screen.getByRole('tab', { name: 'RRC (54)' }));
    await waitFor(() =>
      expect(fetch.mock.calls.some((c) => String(c[0]).includes('reply_type=RRC'))).toBe(true),
    );
  });

  it('?mine=approval (old menu item, alerts) opens the Approvals inbox', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/cases/': page([]), '/meta/': META }));
    renderAt('/:module/cases', '/upi/cases?mine=approval', <CaseQueuePage />);
    expect(await screen.findByText('Approvals inbox')).toBeTruthy();
  });
});

describe('Approvals inbox (7.1)', () => {
  it('Cases (N) | Settings changes (N) from one call; the Cases tab lists the pending cases with their reply', async () => {
    const fetch = routeFetch({
      '/settings/pending/': page([], {
        count: 2,
        can_decide: true,
        cases_awaiting_approval: { count: 5, query: { status: 'PENDING_APPROVAL' } },
      }),
      '/cases/': page([
        makeCase({
          id: 8,
          status: 'PENDING_APPROVAL',
          reply: reply({ source: 'SUBMITTED' }),
          self_approval: true,
        }),
      ]),
      '/meta/': META,
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/:module/approvals', '/upi/approvals', <ApprovalsPage />);
    expect(await screen.findByRole('tab', { name: 'Cases (5)' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Settings changes (2)' })).toBeTruthy();
    const cases = await screen.findByRole('table', { name: 'Cases awaiting approval' });
    expect(await within(cases).findByText('DRC 102 - Online Reversal')).toBeTruthy();
    expect(within(cases).getByText('Your submission')).toBeTruthy();
    expect(within(cases).queryByRole('columnheader', { name: 'Priority' })).toBeNull();
    expect(fetch.mock.calls.some((c) => String(c[0]).includes('status=PENDING_APPROVAL'))).toBe(true);
  });
});

describe('Case buttons name the reply (6.5)', () => {
  it("submit names the rule's reply, approve names the Maker's", () => {
    expect(transitionLabel('submit', { reply: reply(), what_happened: null })).toBe(
      'Submit reply DRC 102 - Online Reversal',
    );
    expect(transitionLabel('approve', { reply: reply({ source: 'SUBMITTED' }), what_happened: null })).toBe(
      'Approve DRC 102 - Online Reversal',
    );
    expect(transitionLabel('approve', { reply: reply({ source: 'RULE' }), what_happened: null })).toBe(
      'Approve',
    );
    expect(transitionLabel('take', { reply: reply(), what_happened: null })).toBe('Take');
  });
});

describe('Response codes by reply type (7.2)', () => {
  it("groups in the server's order; an older row without reply_type goes under its flag", () => {
    const row = (o: Partial<ResponseCodeRow>) => ({ id: 1, flag: 'DRC', ...o }) as ResponseCodeRow;
    const groups = replyGroups(
      [row({ id: 1, reply_type: 'RET' }), row({ id: 2, reply_type: 'DRC' }), row({ id: 3, flag: 'TCC' })],
      [
        { value: 'DRC', label: 'DRC (Debit Reversal Confirmation)' },
        { value: 'RRC', label: 'RRC' },
        { value: 'RET', label: 'RET (Return)' },
      ],
    );
    expect(groups.map((g) => [g.label, g.rows.map((r) => r.id)])).toEqual([
      ['DRC (Debit Reversal Confirmation)', [2]],
      ['RET (Return)', [1]],
      ['TCC', [3]],
    ]);
  });
});

describe('Find an RRN (4.5)', () => {
  const lookup = (o: Partial<RrnLookup> = {}): RrnLookup => ({
    rrn: '900000000101',
    found: true,
    transactions: [],
    cases: [],
    file_rows: [],
    replaced_count: 0,
    ...o,
  });
  const tx = (id: number, date: string) => ({
    id,
    business_date: date,
    is_superseded: false,
    processing_status: 'MATCHED',
    rule_code: 'R01',
    rule_name: 'DRC - debited and already reversed',
    cbs: { present: true, amount: '140.50', direction: 'DEBIT' },
    npci: { present: true, amount: '140.50', direction: 'DEBIT', response_code: 'U3' },
    link: `/upi/dates/${date}/groups/${id}`,
  });

  it('one transaction opens it', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({ '/rrn/900000000101/': lookup({ transactions: [tx(5501, '2026-07-14')] }) }),
    );
    renderAt('/:module/rrn/:rrn', '/upi/rrn/900000000101', <RrnLookupPage />);
    expect(await screen.findByText('Transaction page')).toBeTruthy();
  });

  it('several: lists transactions, cases and file rows; nothing found says so', async () => {
    const fetch = routeFetch({
      '/rrn/900000000101/': lookup({
        transactions: [tx(5501, '2026-07-14'), tx(5400, '2026-07-13')],
        file_rows: [
          {
            register_type: 'DRC',
            business_date: '2026-07-14',
            uploaded_file_id: 88,
            filename: 'Debit Reversal 14.csv',
            amount: '140.50',
            direction: 'DEBIT',
            link: '/upi/files/88',
          },
        ],
      }),
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/:module/rrn/:rrn', "/upi/rrn/'900000000101", <RrnLookupPage />);
    const txs = await screen.findByRole('table', { name: 'Transactions with RRN 900000000101' });
    expect(within(txs).getAllByRole('link', { name: /^Open the transaction of/ })).toHaveLength(2);
    expect(screen.getByRole('link', { name: 'Debit Reversal 14.csv' }).getAttribute('href')).toBe(
      '/upi/files/88',
    );
    // The Excel apostrophe is dropped before asking.
    expect(String(fetch.mock.calls[0]?.[0])).toBe('/api/v1/upi/rrn/900000000101/');
  });

  it('nothing found', async () => {
    vi.stubGlobal('fetch', routeFetch({ '/rrn/1/': lookup({ rrn: '1', found: false }) }));
    renderAt('/:module/rrn/:rrn', '/upi/rrn/1', <RrnLookupPage />);
    expect(await screen.findByText(/No transaction, case or loaded file row has RRN 1\./)).toBeTruthy();
  });
});

describe('Original file (4.6)', () => {
  it("not stored: the server's sentence, plainly; no button", async () => {
    const f = makeFileRow({
      id: 88,
      original: {
        available: false,
        reason: 'NOT_STORED',
        message:
          'The original of this file is not stored by the app (it was loaded outside the upload page).',
        download_url: null,
      },
      downloads: [],
      download_count: 0,
    });
    vi.stubGlobal('fetch', routeFetch({ '/files/88/': f, '/files/88/exceptions/': page([]) }));
    renderAt('/:module/files/:fileId', '/upi/files/88', <FileDetailPage />);
    expect(await screen.findByText(/is not stored by the app/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Download original' })).toBeNull();
  });

  it('stored: Download original, and who downloaded it', async () => {
    const f = makeFileRow({
      id: 89,
      original: {
        available: true,
        reason: null,
        message: '',
        download_url: '/api/v1/upi/files/89/original/',
      },
      downloads: [{ by: 'ops1', at: '2026-07-15T06:00:00Z' }],
      download_count: 1,
    });
    vi.stubGlobal('fetch', routeFetch({ '/files/89/': f, '/files/89/exceptions/': page([]) }));
    renderAt('/:module/files/:fileId', '/upi/files/89', <FileDetailPage />);
    expect(await screen.findByRole('button', { name: 'Download original' })).toBeTruthy();
    expect(screen.getByText('Downloaded once.')).toBeTruthy();
    expect(
      within(screen.getByRole('list', { name: 'Downloads of the original' })).getByText(/ops1/),
    ).toBeTruthy();
  });
});

describe('Settlement in one sentence (5.1 / 5.2)', () => {
  const summary = (o: Partial<SettlementSummary> = {}): SettlementSummary => ({
    status: 'TALLIED',
    status_label: 'Tallied',
    tallied: true,
    reasons: [],
    cbs_net: '-12500.00',
    ntsl_net: '-12500.00',
    difference: '0.00',
    explained: '-300.00',
    unexplained: '0.00',
    cycles_unexplained_amount: '0.00',
    cycles_unexplained_count: 0,
    unexplained_entries: 0,
    explained_by_type: [
      {
        type: 'DRC',
        label: 'DRC (debited, not reversed)',
        count: 2,
        amount: '-200.00',
        in_sop_check: true,
        rules: [],
      },
      { type: 'RET', label: 'RET', count: 1, amount: '-100.00', in_sop_check: true, rules: [] },
      { type: 'LATE_ADJUSTMENTS', label: 'Late', count: 3, amount: '150.00', in_sop_check: false, rules: [] },
    ],
    cbs_rows_without_valid_rrn: null,
    self_check_failed: false,
    settlement_case: null,
    notes: {},
    ...o,
  });

  it('tallied / not tallied', () => {
    expect(settlementSentence(summary())).toBe(
      'Tallied. CBS -Rs 12,500.00 = NTSL -Rs 12,500.00 after -Rs 300.00 explained by DRC (debited, not reversed) and RET.',
    );
    expect(
      settlementSentence(
        summary({
          tallied: false,
          status: 'UNEXPLAINED',
          unexplained: '-200.00',
          unexplained_entries: 2,
          settlement_case: { id: 4600, status: 'OPEN', link: '/upi/cases/4600' },
        }),
      ),
    ).toBe('Not tallied. -Rs 200.00 unexplained (2 entries). See settlement case #4600.');
  });

  it('notes as the server sends them', () => {
    expect(noteText('late_adjustments', { label: 'Late adjustments', count: 3, amount: '150.00' })).toBe(
      'Late adjustments: 3 row(s), Rs 150.00',
    );
    expect(noteText('other_thing', 'kept as sent')).toBe('other thing: kept as sent');
  });
});

describe('SSKUPI step: why there is nothing new (owner 2026-10-27)', () => {
  it('names the file the cases are already in', () => {
    expect(
      nothingNewLine({
        already_sent_files: ['SSKUPI26092026.csv'],
        not_included: [],
        file_date: '2026-09-26',
      }),
    ).toBe('All cases are already in SSKUPI26092026.csv.');
    expect(nothingNewLine({ already_sent_files: [], not_included: [], file_date: '2026-09-26' })).toBe(
      'No reply is waiting to be included in a file for 26-Sep-2026.',
    );
  });
});

describe('Audit "Everything" (9.1)', () => {
  it('offers every event type once', () => {
    const types = allEventTypes({
      modules: [],
      case_event_types: [{ value: 'APPROVED', label: 'Approved' }],
      settings_kinds: [],
      settings_event_types: [
        { value: 'APPROVED', label: 'Approved' },
        { value: 'PROPOSED', label: 'Proposed' },
      ],
      user_activity_types: [{ value: 'LOGIN', label: 'Login' }],
      operation_types: [],
      orderings: [],
    });
    expect(types.map((t) => t.value)).toEqual(['APPROVED', 'PROPOSED', 'LOGIN']);
  });
});
