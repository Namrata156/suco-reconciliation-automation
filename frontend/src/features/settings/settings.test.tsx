import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type {
  CodeChangeRequest,
  MapEntry,
  MapVersion,
  NtslMappingState,
  PendingApproval,
  ResponseCodeRow,
  RoleName,
  StaleToleranceDates,
  ToleranceDetail,
  ToleranceState,
  ToleranceVersion,
} from '../../api/types';
import { MeProvider } from '../../auth/auth';
import { PendingApprovalsPage } from '../../pages/PendingApprovalsPage';
import { json, makeMe, META, renderWithProviders, routeFetch, sentBody } from '../../test/fixtures';
import { editPayload, ResponseCodesTab } from './ResponseCodesTab';
import { mappingPayload, NtslMappingTab } from './NtslMappingTab';
import { ToleranceTab } from './ToleranceTab';

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
const isPost = (init?: RequestInit) => init?.method === 'POST';

function renderAs(roles: RoleName[], path: string, route: string, element: ReactElement) {
  return renderWithProviders(
    <MeProvider me={makeMe(roles)}>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
    </MeProvider>,
    { route },
  );
}

// ---------------------------------------------------------------------------
// tolerance (4.16 c)
// ---------------------------------------------------------------------------
function tol(o: Partial<ToleranceVersion> = {}): ToleranceVersion {
  return {
    id: 1,
    version: 1,
    status: 'ACTIVE',
    amount: '0.00',
    txn_count: 0,
    based_on_version: null,
    reason: 'Seeded',
    proposed_by: null,
    proposed_at: null,
    decided_by: 'checker1',
    decided_at: '2026-10-01T05:00:00Z',
    decision_remarks: '',
    self_approved: false,
    activated_at: '2026-10-01T05:00:00Z',
    retired_at: null,
    self_approval: false,
    ...o,
  };
}
const NO_STALE: StaleToleranceDates = { count: 0, dates: [], note: 'Run each date again.' };
const STALE: StaleToleranceDates = {
  count: 1,
  note: "These dates' settlement used an older tolerance. Run each date again from its overview.",
  dates: [
    {
      business_date: '2026-07-14',
      result_id: 3,
      settlement_status: 'TALLIED',
      computed_at: '2026-07-15T05:03:00Z',
      tolerance_version: 1,
      tolerance: { amount: '0.00', txn_count: 0 },
    },
  ],
};
function tolState(o: Partial<ToleranceState> = {}): ToleranceState {
  return {
    active: tol(),
    default_if_none: { amount: '0.00', txn_count: 0 },
    pending: null,
    can_propose: false,
    can_decide: false,
    note: 'Results already computed keep the tolerance they used.',
    dates_with_older_tolerance: NO_STALE,
    ...o,
  };
}
const pendingTol = (o: Partial<ToleranceDetail> = {}): ToleranceDetail => ({
  ...tol({
    id: 2,
    version: 2,
    status: 'PENDING',
    amount: '1.00',
    proposed_by: 'admin1',
    decided_by: null,
    activated_at: null,
  }),
  events: [
    {
      event_type: 'PROPOSED',
      actor_username: 'admin1',
      created_at: '2026-10-20T05:00:00Z',
      remarks: 'Rounding',
      self_approved: false,
    },
  ],
  ...o,
});

const renderTolerance = (roles: RoleName[]) =>
  renderAs(roles, '/:module/settings/tolerance', '/upi/settings/tolerance', <ToleranceTab />);

describe('Tolerance (design 4.16 c, owner 2026-10-20)', () => {
  it('Administrator proposes amount and count with a reason', async () => {
    let proposed = false;
    const fetch = routeFetch({
      '/settings/tolerance/': (init: RequestInit | undefined) => {
        if (isPost(init)) {
          proposed = true;
          return json(201, pendingTol());
        }
        return json(
          200,
          proposed ? tolState({ can_propose: true, pending: pendingTol() }) : tolState({ can_propose: true }),
        );
      },
      '/settings/tolerance/versions/': page([tol()]),
    });
    vi.stubGlobal('fetch', fetch);
    renderTolerance(['Administrator']);

    await userEvent.click(await screen.findByRole('button', { name: 'Propose tolerance change' }));
    const dialog = await screen.findByRole('dialog');
    const amount = within(dialog).getByRole('textbox', { name: 'Tolerance amount (Rs)' });
    await userEvent.clear(amount);
    await userEvent.type(amount, '1.5x');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit tolerance proposal' }));
    expect(await within(dialog).findByText(/at most 2 decimals/)).toBeTruthy();
    await userEvent.clear(amount);
    await userEvent.type(amount, '1.00');
    await userEvent.type(
      within(dialog).getByRole('textbox', { name: 'Reason' }),
      'Ops asked for Rs 1 rounding',
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit tolerance proposal' }));

    await waitFor(() => expect(proposed).toBe(true));
    const post = fetch.mock.calls.find((c) => isPost(c[1] as RequestInit));
    expect(sentBody(post)).toEqual({ amount: '1.00', txn_count: 0, reason: 'Ops asked for Rs 1 rounding' });
    expect(await screen.findByRole('heading', { name: 'Pending: tolerance v2' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Approve tolerance v2' })).toBeNull(); // not a Checker
    expect(screen.queryByRole('button', { name: 'Propose tolerance change' })).toBeNull(); // one pending at a time
  });

  it('shows the 400 reason of a refused proposal in the form', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/settings/tolerance/': (init: RequestInit | undefined) =>
          isPost(init)
            ? json(400, { detail: 'The proposal changes nothing' })
            : json(200, tolState({ can_propose: true })),
        '/settings/tolerance/versions/': page([tol()]),
      }),
    );
    renderTolerance(['Administrator']);
    await userEvent.click(await screen.findByRole('button', { name: 'Propose tolerance change' }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Reason' }), 'x');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit tolerance proposal' }));
    expect((await within(dialog).findByRole('alert')).textContent).toMatch(/The proposal changes nothing/);
  });

  it('Checker approves; the dates settled with an older tolerance link to their overview (no re-run)', async () => {
    let approved = false;
    const fetch = routeFetch({
      '/settings/tolerance/': () =>
        json(
          200,
          approved
            ? tolState({
                can_decide: true,
                active: tol({ id: 2, version: 2, amount: '1.00' }),
                dates_with_older_tolerance: STALE,
              })
            : tolState({ can_decide: true, pending: pendingTol() }),
        ),
      '/settings/tolerance/2/approve/': () => {
        approved = true;
        return json(200, { ...pendingTol({ status: 'ACTIVE' }), dates_with_older_tolerance: STALE });
      },
      '/settings/tolerance/versions/': page([tol()]),
    });
    vi.stubGlobal('fetch', fetch);
    renderTolerance(['Checker']);

    await userEvent.click(await screen.findByRole('button', { name: 'Approve tolerance v2' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByRole('checkbox')).toBeNull(); // not a self-approval
    await userEvent.click(within(dialog).getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(approved).toBe(true));
    expect(await screen.findByText('Approved: no date was re-run')).toBeTruthy();
    const link = await screen.findByRole('link', { name: 'Open 14-Jul-2026 to run it again' });
    expect(link.getAttribute('href')).toBe('/upi/dates/2026-07-14');
  });

  it('approving your own proposal needs the explicit confirmation (as for cases)', async () => {
    const fetch = routeFetch({
      '/settings/tolerance/': tolState({
        can_decide: true,
        can_propose: true,
        pending: pendingTol({ self_approval: true }),
      }),
      '/settings/tolerance/2/approve/': (init: RequestInit | undefined) =>
        (sentBody([null, init]) as { confirm_self_approval?: boolean }).confirm_self_approval
          ? json(200, {
              ...pendingTol({ status: 'ACTIVE', self_approved: true }),
              dates_with_older_tolerance: NO_STALE,
            })
          : json(400, {
              detail: 'Self',
              errors: { confirm_self_approval: ['Confirm the self-approval to continue.'] },
            }),
      '/settings/tolerance/versions/': page([tol()]),
    });
    vi.stubGlobal('fetch', fetch);
    renderTolerance(['Administrator', 'Checker']);

    await userEvent.click(await screen.findByRole('button', { name: 'Approve tolerance v2' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      within(dialog).getByText(/You proposed this change; approving it will be recorded as self-approved/),
    ).toBeTruthy();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Approve (self-approved)' }));
    expect(await within(dialog).findByText('Confirm the self-approval to continue')).toBeTruthy();
    await userEvent.click(
      within(dialog).getByRole('checkbox', { name: 'I confirm this approval of my own proposal' }),
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Approve (self-approved)' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    const approve = fetch.mock.calls.filter((c) => String(c[0]).endsWith('/approve/'));
    expect(approve).toHaveLength(1); // the client check stopped the unconfirmed request
    expect(sentBody(approve[0])).toEqual({ confirm_self_approval: true });
  });

  it('Auditor reads only', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/settings/tolerance/': tolState({ pending: pendingTol() }),
        '/settings/tolerance/versions/': page([tol()]),
      }),
    );
    renderTolerance(['Auditor']);
    expect(await screen.findByText("Awaiting a Checker's decision.")).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Approve|Reject|Propose/ })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// pending approvals
// ---------------------------------------------------------------------------
const PENDING: PendingApproval[] = [
  {
    kind: 'tolerance',
    kind_label: 'Settlement tolerance change',
    id: 2,
    title: 'Tolerance v2: Rs 1.00 / 0 txn',
    reason: 'Rounding',
    proposed_by: 'admin1',
    proposed_at: '2026-10-20T05:00:00Z',
    self_approval: false,
    detail: tol({ id: 2, version: 2, status: 'PENDING' }),
  },
];

describe('Pending approvals (design 4.16)', () => {
  it('Checker approves a tolerance from the list and sees the dates to run again', async () => {
    let done = false;
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/settings/pending/': () =>
          json(
            200,
            page(done ? [] : PENDING, {
              can_decide: true,
              cases_awaiting_approval: { count: 3, query: { status: 'PENDING_APPROVAL' } },
            }),
          ),
        '/settings/tolerance/2/approve/': () => {
          done = true;
          return json(200, { ...pendingTol({ status: 'ACTIVE' }), dates_with_older_tolerance: STALE });
        },
        '/meta/': META,
      }),
    );
    renderAs(['Checker'], '/:module/approvals', '/upi/approvals', <PendingApprovalsPage />);

    const cases = await screen.findByRole('link', { name: 'Cases awaiting approval: 3. Open the queue' });
    expect(cases.getAttribute('href')).toBe('/upi/cases?status=PENDING_APPROVAL');
    await userEvent.click(screen.getByRole('button', { name: 'Approve Tolerance v2: Rs 1.00 / 0 txn' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Approve' }));
    const stale = await screen.findByRole('table', { name: 'Dates to run again' });
    expect(within(stale).getByRole('link', { name: 'Open 14-Jul-2026 to run it again' })).toBeTruthy();
    expect(await screen.findByText('Nothing is waiting for approval.')).toBeTruthy();
  });

  it('every role reads it; only a Checker gets the buttons', async () => {
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/settings/pending/': page(PENDING, {
          can_decide: false,
          cases_awaiting_approval: { count: 0, query: { status: 'PENDING_APPROVAL' } },
        }),
        '/meta/': META,
      }),
    );
    renderAs(['Maker'], '/:module/approvals', '/upi/approvals', <PendingApprovalsPage />);
    expect(
      await screen.findByRole('link', { name: 'Open Tolerance v2: Rs 1.00 / 0 txn in settings' }),
    ).toBeTruthy();
    expect(screen.getByText('Only a Checker approves or rejects these changes.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Approve/ })).toBeNull();
  });

  it('a 409 (someone decided first) closes the dialog and reloads', async () => {
    const fetch = routeFetch({
      '/settings/pending/': page(PENDING, {
        can_decide: true,
        cases_awaiting_approval: { count: 0, query: {} },
      }),
      '/settings/tolerance/2/reject/': () => json(409, { detail: 'Tolerance v2 is not pending.' }),
      '/meta/': META,
    });
    vi.stubGlobal('fetch', fetch);
    renderAs(['Checker'], '/:module/approvals', '/upi/approvals', <PendingApprovalsPage />);
    await userEvent.click(
      await screen.findByRole('button', { name: 'Reject Tolerance v2: Rs 1.00 / 0 txn' }),
    );
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Reason' }), 'Rs 0 stays');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    // Reloaded after the refusal (the notification itself is a Mantine portal).
    await waitFor(() =>
      expect(
        fetch.mock.calls.filter((c) => String(c[0]).includes('/settings/pending/')).length,
      ).toBeGreaterThan(1),
    );
    const reject = fetch.mock.calls.find((c) => String(c[0]).endsWith('/reject/'));
    expect(sentBody(reject)).toEqual({ reason: 'Rs 0 stays' });
  });
});

// ---------------------------------------------------------------------------
// response codes (4.16 a)
// ---------------------------------------------------------------------------
function code(o: Partial<ResponseCodeRow> = {}): ResponseCodeRow {
  return {
    id: 21,
    flag: 'RET',
    code: null,
    code_known: false,
    description: 'Account closed',
    source: 'NPCI circular',
    notes: '',
    label: 'RET (unknown) - Account closed',
    locked: false,
    used_by: [],
    pending_request_id: null,
    created_at: '2026-09-01T05:00:00Z',
    ...o,
  };
}
const request = (o: Partial<CodeChangeRequest> = {}): CodeChangeRequest => ({
  id: 5,
  kind: 'EDIT',
  flag: 'RET',
  response_code_id: 11,
  changes: { code: [null, '116'] },
  new_source: '',
  reason: 'NPCI circular',
  status: 'PENDING',
  requested_by: 'maker1',
  requested_at: '2026-10-20T05:00:00Z',
  decided_by: null,
  decided_at: null,
  decision_remarks: '',
  self_approved: false,
  self_approval: false,
  response_code_label: 'RET 115 - Beneficiary account closed',
  events: [],
  ...o,
});

describe('Response codes (design 4.16 a)', () => {
  it('sends only the changed fields of an edit', () => {
    const v = { new_code: ' 116 ', new_description: 'Account closed', new_notes: '', reason: ' circular ' };
    expect(editPayload(code(), v)).toEqual({
      kind: 'EDIT',
      response_code: 21,
      new_code: '116',
      reason: 'circular',
    });
    expect(editPayload(code(), { ...v, new_code: '' })).toBeNull();
  });

  it('Maker proposes an edit; the request goes to a Checker', async () => {
    const fetch = routeFetch({
      '/settings/response-codes/': page(
        [
          code(),
          code({
            id: 22,
            code: '102',
            code_known: true,
            flag: 'DRC',
            label: 'DRC 102',
            locked: true,
            used_by: ['R01'],
          }),
        ],
        {
          rule_set: 'v3.2',
          can_propose: true,
          can_decide: false,
        },
      ),
      '/settings/response-codes/requests/': (init: RequestInit | undefined) =>
        isPost(init) ? json(201, request()) : json(200, page([])),
      '/meta/': META,
    });
    vi.stubGlobal('fetch', fetch);
    renderAs(
      ['Maker'],
      '/:module/settings/response-codes',
      '/upi/settings/response-codes',
      <ResponseCodesTab />,
    );

    await userEvent.click(
      await screen.findByRole('button', { name: 'Propose edit of RET (unknown) - Account closed' }),
    );
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Numeric code' }), '116');
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Reason' }), 'NPCI circular');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit code change' }));
    await waitFor(() => expect(fetch.mock.calls.some((c) => isPost(c[1] as RequestInit))).toBe(true));
    const post = fetch.mock.calls.find((c) => isPost(c[1] as RequestInit));
    expect(sentBody(post)).toEqual({
      kind: 'EDIT',
      response_code: 21,
      new_code: '116',
      reason: 'NPCI circular',
    });
  });

  it('Checker approves a pending request; the code is named from meta', async () => {
    const fetch = routeFetch({
      '/settings/response-codes/': page([code()], { rule_set: 'v3.2', can_propose: false, can_decide: true }),
      '/settings/response-codes/requests/': page([request()]),
      '/settings/response-codes/requests/5/approve/': () => json(200, request({ status: 'APPROVED' })),
      '/meta/': META,
    });
    vi.stubGlobal('fetch', fetch);
    renderAs(
      ['Checker'],
      '/:module/settings/response-codes',
      '/upi/settings/response-codes',
      <ResponseCodesTab />,
    );

    const table = await screen.findByRole('table', { name: 'Response code change requests' });
    expect(await within(table).findByText('RET 115 - Beneficiary account closed')).toBeTruthy();
    expect(within(table).getByText('code: - -> 116')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Propose/ })).toBeNull();
    await userEvent.click(within(table).getByRole('button', { name: 'Approve code request #5' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Approve' }));
    await waitFor(() =>
      expect(fetch.mock.calls.some((c) => String(c[0]).endsWith('/requests/5/approve/'))).toBe(true),
    );
  });
});

// ---------------------------------------------------------------------------
// NTSL mapping (4.16 b)
// ---------------------------------------------------------------------------
function mapVersion(o: Partial<MapVersion> = {}): MapVersion {
  return {
    id: 1,
    version: 1,
    status: 'ACTIVE',
    based_on_version: null,
    reason: 'Seeded',
    proposed_by: null,
    proposed_at: null,
    decided_by: 'checker1',
    decided_at: null,
    decision_remarks: '',
    self_approved: false,
    activated_at: '2026-10-01T05:00:00Z',
    retired_at: null,
    entry_count: 2,
    change_counts: { added: 0, removed: 0, recategorised_loaded_descriptions: 0 },
    self_approval: false,
    ...o,
  };
}
const entry = (id: number, pattern: string): MapEntry => ({
  id,
  match_type: 'EXACT',
  pattern,
  category: 'FEE',
  txn_type: '',
  priority: 100,
  notes: '',
  category_label: 'Interchange fee',
});
const mappingState = (o: Partial<NtslMappingState> = {}): NtslMappingState => ({
  active: mapVersion(),
  pending: null,
  categories: META.ntsl_categories,
  can_propose: true,
  can_decide: false,
  ...o,
});

describe('NTSL mapping (design 4.16 b)', () => {
  it('builds the proposal: exact patterns trimmed, regexes verbatim', () => {
    const add = [
      {
        match_type: 'EXACT',
        pattern: ' Fee line ',
        category: 'FEE',
        txn_type: '',
        priority: 100,
        notes: ' n ',
      },
      { match_type: 'REGEX', pattern: ' ^A.*$', category: 'FEE', txn_type: 'U2', priority: 5, notes: '' },
    ];
    expect(mappingPayload({ add, reason: ' r ' }, [7])).toEqual({
      add: [
        {
          match_type: 'EXACT',
          pattern: 'Fee line',
          category: 'FEE',
          txn_type: '',
          priority: 100,
          notes: 'n',
        },
        { match_type: 'REGEX', pattern: ' ^A.*$', category: 'FEE', txn_type: 'U2', priority: 5, notes: '' },
      ],
      remove_entry_ids: [7],
      reason: 'r',
    });
  });

  it('Maker ticks an active entry to remove and proposes', async () => {
    const fetch = routeFetch({
      '/settings/ntsl-mapping/': (init: RequestInit | undefined) =>
        isPost(init)
          ? json(201, {
              ...mapVersion({ id: 2, version: 2, status: 'PENDING' }),
              change_summary: null,
              events: [],
            })
          : json(200, mappingState()),
      '/settings/ntsl-mapping/versions/': page([mapVersion()]),
      '/settings/ntsl-mapping/versions/1/entries/': page([entry(7, 'Interchange Fee'), entry(8, 'GST')]),
    });
    vi.stubGlobal('fetch', fetch);
    renderAs(['Maker'], '/:module/settings/ntsl-mapping', '/upi/settings/ntsl-mapping', <NtslMappingTab />);

    await userEvent.click(await screen.findByRole('checkbox', { name: 'Remove entry #7' }));
    await userEvent.click(screen.getByRole('button', { name: 'Propose mapping change (1 to remove)' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/#7 EXACT "Interchange Fee"/)).toBeTruthy();
    await userEvent.type(within(dialog).getByRole('textbox', { name: 'Reason' }), 'Duplicate line');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Submit mapping proposal' }));
    await waitFor(() => expect(fetch.mock.calls.some((c) => isPost(c[1] as RequestInit))).toBe(true));
    const post = fetch.mock.calls.find((c) => isPost(c[1] as RequestInit));
    expect(sentBody(post)).toEqual({ add: [], remove_entry_ids: [7], reason: 'Duplicate line' });
  });

  it('Checker sees the pending change summary and the decision buttons', async () => {
    const pending = mapVersion({
      id: 2,
      version: 2,
      status: 'PENDING',
      based_on_version: 1,
      proposed_by: 'maker1',
      change_counts: { added: 1, removed: 0, recategorised_loaded_descriptions: 0 },
    });
    vi.stubGlobal(
      'fetch',
      routeFetch({
        '/settings/ntsl-mapping/': mappingState({ pending, can_propose: false, can_decide: true }),
        '/settings/ntsl-mapping/versions/': page([pending, mapVersion()]),
        '/settings/ntsl-mapping/versions/2/': {
          ...pending,
          change_summary: {
            added: [
              {
                match_type: 'EXACT',
                pattern: 'New fee',
                category: 'FEE',
                txn_type: '',
                priority: 100,
                notes: '',
              },
            ],
            removed: [],
          },
          events: [],
        },
        '/settings/ntsl-mapping/versions/1/entries/': page([entry(7, 'Interchange Fee')]),
      }),
    );
    renderAs(['Checker'], '/:module/settings/ntsl-mapping', '/upi/settings/ntsl-mapping', <NtslMappingTab />);
    expect(await screen.findByText(/EXACT "New fee" - Interchange fee/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Approve NTSL mapping v2' })).toBeTruthy();
    expect(screen.queryByRole('checkbox', { name: /Remove entry/ })).toBeNull();
  });
});
