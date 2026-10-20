/** Test data shaped like the real /api/v1/ responses (api/views.py, api/auth.py). */
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter } from 'react-router';

import type {
  ActionTypeMeta,
  BusinessDateOverview,
  CaseListItem,
  FileTypeOption,
  Me,
  ModuleMeta,
  Permissions,
  ReconciliationRunInfo,
  RoleName,
  UploadedFileDetail,
} from '../api/types';
import { makeQueryClient, Providers } from '../App';

/** api/base.py CAPABILITIES, so menu tests use the server's own matrix. */
const CAPS: Record<keyof Permissions, RoleName[]> = {
  'cases.view': ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'],
  'cases.work': ['Maker'],
  'cases.approve': ['Checker'],
  'cases.reassign': ['Administrator'],
  'files.view': ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'],
  'files.upload': ['Operations User'],
  'results.view': ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'],
  'settings.view': ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'],
  'settings.propose_codes': ['Maker'],
  'settings.propose_mapping': ['Maker'],
  'settings.propose_tolerance': ['Administrator'],
  'settings.approve': ['Checker'],
  'alerts.view': ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'],
  'audit.view': ['Administrator', 'Auditor'],
  'users.admin': ['Administrator'],
};

export function permissionsFor(roles: RoleName[]): Permissions {
  return Object.fromEntries(
    Object.entries(CAPS).map(([cap, allowed]) => [cap, allowed.some((r) => roles.includes(r))]),
  ) as unknown as Permissions;
}

export function makeMe(roles: RoleName[], over: Partial<Me> = {}): Me {
  return {
    id: 7,
    username: 'maker1',
    full_name: 'Maker One',
    roles,
    permissions: permissionsFor(roles),
    modules: [{ code: 'UPI', slug: 'upi', name: 'Unified Payments Interface' }],
    admin_url: roles.includes('Administrator') ? '/admin/' : null,
    session: { idle_timeout_seconds: 900, warning_seconds: 120, expires_at_browser_close: true },
    ...over,
  };
}

const spec = (value: string, label: string, o: Partial<ActionTypeMeta> = {}): ActionTypeMeta => ({
  value,
  label,
  code_flag: null,
  fixed_code: null,
  requires_cbs_reversal_evidence: false,
  requires_remarks: false,
  completes_on_approval: false,
  settlement_only: false,
  ...o,
});

export const ACTION_TYPES: ActionTypeMeta[] = [
  spec('DRC_102', 'DRC reply 102 (customer reversed online)', { code_flag: 'DRC', fixed_code: '102' }),
  spec('RET', 'RET - return to remitter (with reason)', { code_flag: 'RET' }),
  spec('RRC_501_NO_CREDIT', 'RRC 501 without a new credit (CBS reversal already posted)', {
    code_flag: 'RRC',
    fixed_code: '501',
    requires_cbs_reversal_evidence: true,
  }),
  spec('CLOSE_NO_ACTION', 'Close without action (with finding)', {
    requires_remarks: true,
    completes_on_approval: true,
  }),
  spec('SETTLEMENT_EXPLANATION', 'Settlement difference explained (no money moves)', {
    requires_remarks: true,
    completes_on_approval: true,
    settlement_only: true,
  }),
];

export const META: ModuleMeta = {
  module: { code: 'UPI', slug: 'upi', name: 'Unified Payments Interface' },
  rule_set: 'v3.2',
  rules: [
    {
      code: 'R01',
      name: 'Debited and reversed',
      priority: 'LOW',
      status_category: 'REVERSAL_REQUIRED',
      default_action_type: 'DRC_102',
    },
    {
      code: 'R13',
      name: 'Investigate',
      priority: 'MEDIUM',
      status_category: 'PENDING_INVESTIGATION',
      default_action_type: '',
    },
  ],
  case_statuses: [
    { value: 'OPEN', label: 'Open' },
    { value: 'IN_PROGRESS', label: 'In progress' },
    { value: 'PENDING_APPROVAL', label: 'Pending approval' },
    { value: 'APPROVED', label: 'Approved' },
    { value: 'COMPLETED', label: 'Completed' },
  ],
  active_statuses: ['OPEN', 'IN_PROGRESS', 'PENDING_APPROVAL', 'APPROVED'],
  case_types: [
    { value: 'TRANSACTION', label: 'Transaction' },
    { value: 'SETTLEMENT', label: 'Settlement' },
  ],
  priorities: [
    { value: 'HIGH', label: 'High' },
    { value: 'MEDIUM', label: 'Medium' },
    { value: 'LOW', label: 'Low' },
  ],
  status_categories: [],
  flags: [],
  decisions: [],
  event_types: [],
  action_types: ACTION_TYPES,
  response_codes: [
    {
      id: 11,
      flag: 'RET',
      code: '115',
      description: 'Beneficiary account closed',
      label: 'RET 115 - Beneficiary account closed',
    },
    { id: 12, flag: 'DRC', code: '102', description: 'Reversed online', label: 'DRC 102 - Reversed online' },
  ],
  ageing_buckets: [],
  file_types: [],
  settlement_cycles: [],
  transitions: [],
  bulk_actions: ['take', 'submit', 'approve'],
  bulk_priorities: ['LOW', 'MEDIUM'],
  case_orderings: ['age', 'business_date', 'due_date', 'priority'],
  self_approval_message: 'You submitted this action; approving it will be recorded as self-approved.',
};

const NO_FLAGS = {
  settlement: false,
  overdue: false,
  r07x: false,
  needs_review: false,
  multiple_adjustment_rows: false,
  rrc_already_sent: false,
  no_deadline: false,
  previous_day_r12: false,
};

let nextId = 100;
export function makeCase(over: Partial<CaseListItem> = {}): CaseListItem {
  nextId += 1;
  return {
    id: nextId,
    payment_module: 'UPI',
    self_approval: false,
    business_date: '2026-07-14',
    case_type: 'TRANSACTION',
    rrn: `4195${nextId}`,
    rule_code: 'R01',
    status_category: 'REVERSAL_REQUIRED',
    priority: 'LOW',
    status: 'OPEN',
    due_date: '2026-07-16',
    due_cutoff_time: null,
    age_days: 1,
    ageing_bucket: '0-2 days',
    is_overdue: false,
    assigned_to: null,
    default_action_type: 'DRC_102',
    flags: { ...NO_FLAGS },
    rrc_already_sent_on: null,
    ...over,
    ...(over.flags ? { flags: { ...NO_FLAGS, ...over.flags } } : {}),
  };
}

// --- Slice 2: files, dates, runs (api/operations.py shapes) -----------------
const ft = (id: number, code: string, label: string, o: Partial<FileTypeOption> = {}): FileTypeOption => ({
  id,
  file_type_code: code,
  version: 1,
  register_type: code,
  label,
  payload_format: 'CSV',
  is_cycle_wise: false,
  cycles: [],
  date_mode: 'EXACT_MATCH',
  ...o,
});
const NUMBERED = ['1C', '2C', '3C', '4C', '5C', '6C', '7C', '8C', '9C', '10C'];

export const FILE_TYPES: FileTypeOption[] = [
  ft(1, 'CBS_GL_EXTRACT', 'CBS GL Extract', { payload_format: 'SPREADSHEETML_XML', date_mode: 'RANGE_CONTAINS' }),
  ft(2, 'DRC', 'Debit Reversal Confirmation Report'),
  ft(3, 'RAW_DATA', 'Raw Data ACQ', { is_cycle_wise: true, cycles: NUMBERED }),
  ft(4, 'RAW_DATA', 'Raw Data ISS', { is_cycle_wise: true, cycles: NUMBERED }),
  ft(5, 'NTSL', 'NTSL', {
    payload_format: 'OOXML_ZIP',
    is_cycle_wise: true,
    cycles: [...NUMBERED, 'DC1', 'DC2'],
  }),
];

let nextFileId = 500;
export function makeFileRow(over: Partial<UploadedFileDetail> = {}): UploadedFileDetail {
  nextFileId += 1;
  return {
    id: nextFileId,
    filename: `UPIRAWDATAACQSSK140726_1C.csv`,
    file_type: { id: 3, file_type_code: 'RAW_DATA', version: 1, register_type: 'RAW_DATA', label: 'Raw Data ACQ' },
    cycle: '1C',
    business_date: '2026-07-14',
    batch_business_date: '2026-07-14',
    processing_status: 'LOADED',
    processing_status_label: 'Loaded',
    state: 'LOADED',
    parser_status: '',
    detected_format: 'CSV',
    size_bytes: 2048,
    sha256: 'ab'.repeat(32),
    source_system: 'manual web upload',
    uploaded_by: 'ops1',
    uploaded_at: '2026-07-15T04:30:00Z',
    valid_record_count: 10,
    rejected_record_count: 0,
    is_current: true,
    is_reprocess: false,
    exception_counts: { INFO: 0, WARNING: 0, ERROR: 0 },
    date_selection: null,
    history: [],
    ...over,
  };
}

export function makeRun(over: Partial<ReconciliationRunInfo> = {}): ReconciliationRunInfo {
  return {
    id: 9,
    business_date: '2026-07-14',
    status: 'SUCCEEDED',
    current_step: '',
    steps: { PRESENCE: { status: 'DONE' }, RULES: { status: 'DONE' }, SETTLEMENT: { status: 'DONE' } },
    error: '',
    requested_by: 'ops1',
    source: 'api',
    requested_at: '2026-07-15T05:00:00Z',
    started_at: '2026-07-15T05:00:01Z',
    finished_at: '2026-07-15T05:03:00Z',
    ...over,
  };
}

export function makeDateOverview(over: Partial<BusinessDateOverview> = {}): BusinessDateOverview {
  return {
    business_date: '2026-07-14',
    files: {
      expected: 66,
      loaded: 58,
      processing: 0,
      awaiting_date_selection: 0,
      failed: 0,
      missing: 8,
      files_received: 59,
      files_loaded: 59,
      files_processing: 0,
      files_awaiting_date_selection: 0,
      files_failed: 0,
    },
    missing: [{ file_type_config_id: 6, label: 'Merchant Raw Data (BBP) ACQ', cycle: '1C' }],
    matrix: { built: false, match_groups: 0, built_at: null },
    rule_evaluation: null,
    cases: { total: 0, active: 0 },
    settlement: null,
    last_run: null,
    run_blockers: [],
    ...over,
  };
}

export function renderWithProviders(ui: ReactElement, { route = '/' }: { route?: string } = {}) {
  const client = makeQueryClient();
  return {
    client,
    ...render(
      <Providers client={client}>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </Providers>,
    ),
  };
}
