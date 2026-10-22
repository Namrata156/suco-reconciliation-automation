/**
 * API shapes for /api/v1/ (design docs/phase5_design_draft_v1.txt, section 3/4).
 *
 * Reconciled 2026-10-15 against the built backend: api/auth.py, api/base.py,
 * api/views.py (v1) and exception_management/api.py (the serializers v1
 * extends). Slice 2 shapes reconciled 2026-10-19 against api/operations.py
 * and api/tests/test_slice2.py. Every API call lives in ./client.ts, so a
 * changed shape is fixed in these two files.
 */

/** Role names exactly as the Django groups (exception_management/roles.py). */
export type RoleName = 'Administrator' | 'Operations User' | 'Maker' | 'Checker' | 'Auditor';

/** {value, label} pair for a Django TextChoices entry. */
export interface Choice {
  value: string;
  label: string;
}

// ---------------------------------------------------------------------------
// auth / me / modules  (api/auth.py, api/base.py)
// ---------------------------------------------------------------------------
export interface PaymentModuleRef {
  /** PaymentModule.code, e.g. "UPI". */
  code: string;
  /** The code in lower case, as used in URLs ("upi"). */
  slug: string;
  name: string;
}

/** The role matrix of design 3.5 as data (api/base.py CAPABILITIES). */
export interface Permissions {
  'cases.view': boolean;
  'cases.work': boolean;
  'cases.approve': boolean;
  'cases.reassign': boolean;
  'files.view': boolean;
  'files.upload': boolean;
  'results.view': boolean;
  'settings.view': boolean;
  'settings.propose_codes': boolean;
  'settings.propose_mapping': boolean;
  'settings.propose_tolerance': boolean;
  'settings.approve': boolean;
  'alerts.view': boolean;
  'audit.view': boolean;
  'users.admin': boolean;
}
export type Capability = keyof Permissions;

/** GET /api/v1/me/ and the body of a successful POST /api/v1/auth/login/. */
export interface Me {
  id: number;
  username: string;
  full_name: string;
  roles: RoleName[];
  permissions: Permissions;
  modules: PaymentModuleRef[];
  /** "/admin/" for an Administrator with staff status, else null. */
  admin_url: string | null;
  session: {
    idle_timeout_seconds: number;
    warning_seconds: number;
    expires_at_browser_close: boolean;
  };
}

/** GET /api/v1/users/?role=Maker (paginated). id = reassign's to_user. */
export interface UserRef {
  id: number;
  username: string;
  full_name: string;
  roles: RoleName[];
}

// ---------------------------------------------------------------------------
// meta  (api/views.py MetaView)
// ---------------------------------------------------------------------------
export interface RuleMeta {
  code: string;
  name: string;
  priority: string;
  status_category: string;
  /** "" when the rule has no pre-filled action. */
  default_action_type: string;
}

export interface AgeingBucketMeta {
  label: string;
  min_days: number;
  max_days: number | null;
}
/** One entry of exception_management.actions ACTION_SPECS, plus its label. */
export interface ActionTypeMeta {
  value: string;
  label: string;
  /** DRC / TCC / RET / RRC, or null when sent without a response code. */
  code_flag: string | null;
  /** The fixed numeric code (e.g. "102"); null with a flag = maker chooses (RET). */
  fixed_code: string | null;
  requires_cbs_reversal_evidence: boolean;
  requires_remarks: boolean;
  completes_on_approval: boolean;
  /** True for SETTLEMENT_EXPLANATION (settlement cases only). */
  settlement_only: boolean;
}

export interface ResponseCodeMeta {
  id: number;
  flag: string;
  code: string | null;
  description: string;
  /** str(ResponseCode), as the case detail shows it. */
  label: string;
}

export interface ModuleMeta {
  module: PaymentModuleRef;
  /** Version label of the active rule set, or null. */
  rule_set: string | null;
  rules: RuleMeta[];
  case_statuses: Choice[];
  /** The statuses the queue shows by default (ACTIVE_STATUSES). */
  active_statuses: string[];
  priorities: Choice[];
  status_categories: Choice[];
  case_types: Choice[];
  flags: Choice[];
  decisions: Choice[];
  event_types: Choice[];
  action_types: ActionTypeMeta[];
  response_codes: ResponseCodeMeta[];
  ageing_buckets: AgeingBucketMeta[];
  file_types: { code: string; register_type: string; is_cycle_wise: boolean; cycles: string[] }[];
  settlement_cycles: { code: string; type: string; start_time: string; end_time: string }[];
  transitions: Choice[];
  bulk_actions: string[];
  bulk_priorities: string[];
  /** Field names the queue's "ordering" accepts; "-" prefix reverses; comma list. */
  case_orderings: string[];
  self_approval_message: string;
  // --- Slice 3 additions (api/views.py MetaView) ---------------------------
  /** Every active FileTypeConfig (ACQ and ISS apart); id = files list ?file_type_config=. */
  file_type_configs: FileTypeOption[];
  /** file_processing ProcessingStatus in pipeline order, with the 4-way screen state. */
  processing_statuses: { value: ProcessingStatus; label: string; state: FileState }[];
  /** reconciliation ProcessingStatus of a match group. */
  match_group_statuses: Choice[];
  /** Results list ?presence= values. */
  presence_patterns: Choice[];
  /** Field names the results list's "ordering" accepts; "-" prefix reverses. */
  result_orderings: string[];
  settlement_statuses: Choice[];
  /** NTSL self-check per cycle: PASS / FAIL / MISSING. */
  self_check_statuses: Choice[];
  settlement_sides: Choice[];
  /** Contributing-transaction kinds: npci / sop / adjustments / z. */
  settlement_transaction_kinds: Choice[];
  ntsl_categories: Choice[];
  code_flags: Choice[];
  /** NTSL mapping / tolerance version statuses (PENDING / ACTIVE / RETIRED / REJECTED). */
  settings_statuses: Choice[];
  /** Response-code change request statuses (PENDING / APPROVED / REJECTED). */
  change_request_statuses: Choice[];
  /** Pending-approvals ?kind= values. */
  settings_kinds: { value: SettingsKind; label: string }[];
  // --- Slice 4 fit items (contract D1) -------------------------------------
  /** settlement MatchType: EXACT / REGEX. */
  ntsl_match_types: Choice[];
  /** settlement TxnType: "" (none, label "-") / U2 / U3. */
  ntsl_txn_types: Choice[];
}

// ---------------------------------------------------------------------------
// summary  (api/views.py SummaryView; design 4.3)
// ---------------------------------------------------------------------------
/** A home-page count and the case-list filter that reproduces it (null = none). */
export interface SummaryCount {
  count: number;
  query: Record<string, string> | null;
}

export interface ModuleSummary {
  /** ISO date (IST) the counts were taken for. */
  business_date_today: string;
  roles: RoleName[];
  /** Maker block (also to Administrator / Auditor). */
  maker?: {
    high_open: SummaryCount;
    high_overdue: SummaryCount;
    overdue: SummaryCount;
    due_today: SummaryCount;
    approved_waiting: SummaryCount;
    mine: SummaryCount;
    mine_overdue: SummaryCount;
  };
  /** Checker block (also to Administrator / Auditor). */
  checker?: {
    awaiting: SummaryCount;
    awaiting_high: SummaryCount;
    awaiting_submitted_by_me: SummaryCount;
    needs_review: SummaryCount;
    /** Pending settings proposals (screens in Slice 3): counts only. */
    settings_awaiting: { response_codes: number; ntsl_mapping: number; tolerance: number };
  };
  /** Operations block (also to Administrator / Auditor). */
  operations?: {
    latest_business_date: string | null;
    files_received: number;
    files_loaded: number;
    files_failed: number;
    /** Expected type x cycle slots with no file (the date checklist, Slice 2); null with no date. */
    files_missing: number | null;
    awaiting_date_selection: number;
    settlement_status: string | null;
  };
}

// ---------------------------------------------------------------------------
// cases  (EXISTS in /api/exceptions/; v1 adds payment_module, design 3.2)
// ---------------------------------------------------------------------------
export interface CaseFlags {
  settlement: boolean;
  overdue: boolean;
  r07x: boolean;
  needs_review: boolean;
  multiple_adjustment_rows: boolean;
  rrc_already_sent: boolean;
  no_deadline: boolean;
  previous_day_r12: boolean;
}

export interface CaseListItem {
  id: number;
  /** PaymentModule.code (v1 addition). */
  payment_module: string;
  /** True when the signed-in user submitted the pending action (approving = self-approved). */
  self_approval: boolean;
  business_date: string;
  case_type: 'TRANSACTION' | 'SETTLEMENT';
  /** "" for a settlement case. */
  rrn: string;
  rule_code: string;
  status_category: string;
  priority: string;
  status: string;
  due_date: string | null;
  due_cutoff_time: string | null;
  age_days: number | null;
  ageing_bucket: string | null;
  is_overdue: boolean | null;
  /** Username of the holder, or null. */
  assigned_to: string | null;
  default_action_type: string;
  flags: CaseFlags;
  rrc_already_sent_on: string | null;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface RuleResultInfo {
  id: number;
  rule_code: string;
  rule_set: string;
  action: string;
  reason: string;
  tat_label: string;
  evaluated_at: string;
  facts: Record<string, unknown>;
  is_superseded: boolean;
}

export interface OverlayRow {
  leg: string;
  fact_id: number;
  file_date: string;
  amount: string | null;
  direction: string;
  response_code: string;
  ssk_side: string;
  dispute_type: string;
  txn_date: string;
  original_not_loaded: boolean | null;
  belongs_to_case_date: boolean;
}

export interface SettlementCycleRow {
  cycle: string | null;
  side: string;
  ntsl_amount: string | null;
  ntsl_count: number | null;
  matched_amount: string | null;
  matched_count: number | null;
  explained_amount: string | null;
  explained_count: number | null;
  unexplained_amount: string | null;
  unexplained_count: number | null;
  within_tolerance: boolean | null;
}

/** exception_management.detail settlement_result_payload (case detail and the per-date screen). */
export interface SettlementResultBody {
  result_id: number;
  result_is_current: boolean;
  status: string;
  computed_at: string;
  reasons: string[] | Record<string, unknown>;
  tolerance: { amount: string | null; txn_count: number | null };
  sop: {
    cbs_net: string | null;
    ntsl_net: string | null;
    difference: string | null;
    explained: string | null;
    unexplained: string | null;
  };
  fees_net: string | null;
  cycles: SettlementCycleRow[];
}

/** The case detail's settlement block. */
export interface SettlementInfo extends SettlementResultBody {
  first_result_id: number;
}

export interface ProposedActionInfo {
  id: number;
  rule_result: number | null;
  rule_code: string;
  action_type: string;
  /** str(ResponseCode) or null. */
  response_code: string | null;
  remarks: string;
  evidence_cbs_reversal_date: string | null;
  evidence_cbs_reversal_amount: string | null;
  evidence_cbs_reversal_rrn: string;
  evidence_notes: string;
  submitted_by: string;
  submitted_at: string;
  decision: 'PENDING' | 'APPROVED' | 'REJECTED' | 'WITHDRAWN';
  decided_by: string | null;
  decided_at: string | null;
  decision_remarks: string;
  self_approved: boolean;
  external_reference: string;
  completed_at: string | null;
}

export interface CaseEventInfo {
  id: number;
  created_at: string;
  event_type: string;
  actor_username: string;
  source: string;
  old_status: string;
  new_status: string;
  remarks: string;
  changes: Record<string, unknown>;
  action_snapshot: Record<string, unknown> | null;
}

export type TransitionName =
  'take' | 'release' | 'reassign' | 'submit' | 'approve' | 'reject' | 'complete' | 'remark' | 'clear_review';

export interface CaseDetail extends CaseListItem {
  /** str(ResponseCode) of the rule's pre-filled code, or null. */
  default_response_code: string | null;
  needs_review: boolean;
  needs_review_reason: string;
  multiple_adjustment_rows: boolean;
  no_deadline: boolean;
  result_missing: boolean;
  created_at: string;
  updated_at: string;
  rule_result: RuleResultInfo | null;
  warnings: string[];
  overlay_rows: OverlayRow[];
  settlement: SettlementInfo | null;
  actions: ProposedActionInfo[];
  events: CaseEventInfo[];
  available_transitions: TransitionName[];
}

/** Queue filters, as query parameters (design 4.4 / api.py CaseListView). */
export interface CaseFilters {
  business_date?: string;
  /** "" = active (server default), "all", or comma-separated statuses. */
  status?: string;
  priority?: string;
  rule_code?: string;
  case_type?: string;
  overdue?: '' | 'true' | 'false';
  mine?: '' | 'assigned' | 'approval';
  flag?: string;
  rrn?: string;
  /** ISO date (v1 addition). */
  due_date?: string;
  /** Comma list of meta.case_orderings; "-" reverses; "" = queue order. */
  ordering?: string;
  page?: number;
  page_size?: number;
}

// ---------------------------------------------------------------------------
// transition / bulk payloads (fields of exception_management/forms.py)
// ---------------------------------------------------------------------------
export interface RemarksPayload {
  remarks?: string;
}
/** Approve: confirm_self_approval is required (server-enforced) for your own submission. */
export interface ApprovePayload extends RemarksPayload {
  confirm_self_approval?: boolean;
}
export interface ReassignPayload {
  to_user: number;
  remarks?: string;
}
export interface SubmitPayload {
  action_type?: string;
  response_code?: number;
  remarks?: string;
  evidence_cbs_reversal_date?: string;
  evidence_cbs_reversal_amount?: string;
  evidence_cbs_reversal_rrn?: string;
  evidence_notes?: string;
}
export interface RejectPayload {
  reason: string;
}
export interface CompletePayload {
  external_reference: string;
  remarks?: string;
}
export interface RemarkPayload {
  text: string;
}
export interface ClearReviewPayload {
  remarks: string;
}

export interface TransitionPayloads {
  take: RemarksPayload;
  release: RemarksPayload;
  reassign: ReassignPayload;
  submit: SubmitPayload;
  approve: ApprovePayload;
  reject: RejectPayload;
  complete: CompletePayload;
  remark: RemarkPayload;
  clear_review: ClearReviewPayload;
}

export type BulkName = 'take' | 'submit' | 'approve';

export interface BulkPayloads {
  take: { case_ids: number[] };
  submit: { case_ids: number[]; action_type?: string; response_code?: number; remarks?: string };
  approve: { case_ids: number[]; remarks?: string; confirm_self_approval?: boolean };
}

/** EXISTS: {"applied": n, "case_ids": [...]}. */
export interface BulkResult {
  applied: number;
  case_ids: number[];
}

// ---------------------------------------------------------------------------
// files, CBS date, business dates and the run  (api/operations.py, Slice 2;
// services file_processing/intake.py, reconciliation/overview.py, runs.py)
// ---------------------------------------------------------------------------
/** UploadedFile.processing_status (file_processing/models.py ProcessingStatus). */
export type ProcessingStatus =
  | 'RECEIVED'
  | 'PARSED'
  | 'VALIDATED'
  | 'VALIDATION_FAILED'
  | 'STAGED'
  | 'AWAITING_DATE_SELECTION'
  | 'NORMALIZED'
  | 'LOADED'
  | 'FAILED';

/** The status folded into four (intake._file_state); a checklist slot adds MISSING. */
export type FileState = 'LOADED' | 'PROCESSING' | 'AWAITING_DATE_SELECTION' | 'FAILED';
export type SlotState = FileState | 'MISSING';

/** EXACT_MATCH: one date, in the name and every row. RANGE_CONTAINS: CBS, date picked later. */
export type DateMode = 'EXACT_MATCH' | 'RANGE_CONTAINS';

/** One active FileTypeConfig (intake.config_payload); id = upload's file_type_config_id. */
export interface FileTypeOption {
  id: number;
  file_type_code: string;
  version: number;
  register_type: string;
  /** "Raw Data ACQ", "CBS GL Extract" ... */
  label: string;
  payload_format: string;
  is_cycle_wise: boolean;
  /** Cycle codes when cycle-wise, else []. */
  cycles: string[];
  date_mode: DateMode;
}

export interface DetectProposal {
  file_type_config_id: number;
  file_type_code: string;
  label: string;
  cycle: string | null;
  /** ISO date read from the file name (EXACT_MATCH types only), or null. */
  business_date: string | null;
  date_mode: DateMode;
  matched_by: 'filename' | 'content';
}

export interface DetectFile {
  /** Position in the list sent (same order as the files). */
  index: number;
  filename: string;
  /** Sniffed content format ("CSV", "SPREADSHEETML_XML", "ZIP" for a partial zip head ...). */
  detected_format: string;
  /** Null: the type could not be recognised; the user chooses it. */
  proposal: DetectProposal | null;
  /** Set when the SHA-256 sent matches a file uploaded before. */
  duplicate: {
    uploaded_file_id: number;
    filename: string;
    uploaded_at: string;
    uploaded_by: string | null;
    message: string;
  } | null;
  warnings: string[];
}

/** POST /files/detect/ (dry run, nothing saved). */
export interface DetectResult {
  /** The date most file names carry: the proposed business date of the upload. */
  business_date: string | null;
  business_dates_found: string[];
  /** Batch-level warnings (mixed dates, archived date). */
  warnings: string[];
  files: DetectFile[];
  file_types: FileTypeOption[];
  max_file_bytes: number;
  detect_head_bytes: number;
}

/** What the CBS date picker shows (intake.date_selection); null unless AWAITING_DATE_SELECTION. */
export interface DateSelectionInfo {
  detected_dates: string[];
  batch_business_date: string | null;
  /** The batch date when it is one of the detected dates, else null. */
  preselected: string | null;
}

/** One row of GET /files/ (operations.file_row). */
export interface UploadedFileRow {
  id: number;
  filename: string;
  file_type: { id: number; file_type_code: string; version: number; register_type: string; label: string };
  cycle: string | null;
  /** Null for CBS until its date is picked. */
  business_date: string | null;
  /** The date the file was uploaded with. */
  batch_business_date: string | null;
  processing_status: ProcessingStatus;
  processing_status_label: string;
  state: FileState;
  parser_status: string;
  detected_format: string;
  size_bytes: number | null;
  sha256: string;
  source_system: string;
  uploaded_by: string | null;
  uploaded_at: string;
  valid_record_count: number | null;
  rejected_record_count: number | null;
  is_current: boolean;
  is_reprocess: boolean;
  exception_counts: { INFO: number; WARNING: number; ERROR: number } | null;
  date_selection: DateSelectionInfo | null;
}

/** One core.OperationLog row (who uploaded / picked the date / started the run). */
export interface OperationLogEntry {
  id: number;
  operation: 'FILE_UPLOADED' | 'FILE_REFUSED' | 'BUSINESS_DATE_SELECTED' | 'RUN_STARTED' | string;
  operation_label: string;
  username: string;
  created_at: string;
  source: string;
  business_date: string | null;
  uploaded_file_id: number | null;
  run_id: number | null;
  details: Record<string, unknown>;
}

export interface UploadedFileDetail extends UploadedFileRow {
  /** Oldest first. */
  history: OperationLogEntry[];
}

/** GET /files/: paginated, plus how many files of the module are still being processed. */
export interface FileListPage extends Paginated<UploadedFileRow> {
  processing_count: number;
}

export interface FileFilters {
  business_date?: string;
  /** Comma list of file_type_code. */
  file_type?: string;
  /** Comma list of FileTypeConfig ids (one side: Raw Data ACQ vs ISS; meta.file_type_configs). */
  file_type_config?: string;
  /** Comma list of cycle codes. */
  cycle?: string;
  /** Comma list of ProcessingStatus. */
  status?: string;
  current?: '' | 'true' | 'false';
  /** uploaded_at, business_date, filename, processing_status, file_type; "-" reverses. */
  ordering?: string;
  page?: number;
  page_size?: number;
}

export interface FileValidationExceptionInfo {
  id: number;
  rule_code: string;
  severity: 'INFO' | 'WARNING' | 'ERROR';
  message: string;
  row_reference: string;
  detected_at: string;
  resolved: boolean;
}

/** POST /files/ (201, or 400 when no file was accepted: same body plus detail). */
export interface UploadResult {
  business_date: string;
  accepted: UploadedFileRow[];
  /** index = position in the files sent with that request. */
  refused: { index: number; filename: string; errors: string[] }[];
  detail?: string;
}

export interface UploadItem {
  file_type_config_id: number;
  cycle: string | null;
}

export type RunStatus = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED';
export type RunStepName = 'PRESENCE' | 'RULES' | 'SETTLEMENT';

/** reconciliation.runs.run_payload. */
export interface ReconciliationRunInfo {
  id: number;
  business_date: string;
  status: RunStatus;
  /** "" when not running. */
  current_step: RunStepName | '';
  steps: Partial<
    Record<
      RunStepName,
      {
        status: 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED';
        started_at?: string;
        finished_at?: string;
        summary?: Record<string, unknown>;
      }
    >
  >;
  error: string;
  requested_by: string;
  source: string;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
}

/** Slot counts (expected type x cycle), then file counts (intake.date_checklists summary). */
export interface ChecklistSummary {
  expected: number;
  loaded: number;
  processing: number;
  awaiting_date_selection: number;
  failed: number;
  missing: number;
  files_received: number;
  files_loaded: number;
  files_processing: number;
  files_awaiting_date_selection: number;
  files_failed: number;
}

/** Why a run would be refused ([] = it may start; reconciliation.runs.blockers_from). */
export interface RunBlocker {
  code: 'RUN_IN_PROGRESS' | 'FILES_PROCESSING' | 'NOTHING_LOADED' | string;
  message: string;
}

/** One row of GET /dates/ (reconciliation.overview.date_overviews). */
export interface BusinessDateOverview {
  business_date: string;
  files: ChecklistSummary;
  missing: { file_type_config_id: number; label: string; cycle: string | null }[];
  matrix: { built: boolean; match_groups: number; built_at: string | null };
  rule_evaluation: {
    id: number;
    rule_set: string;
    started_at: string;
    finished_at: string | null;
    group_count: number;
    triggered_by: string;
  } | null;
  cases: { total: number; active: number };
  settlement: {
    id: number;
    status: string;
    reasons: string[] | Record<string, unknown>;
    computed_at: string;
    unexplained_amount: string;
  } | null;
  last_run: ReconciliationRunInfo | null;
  run_blockers: RunBlocker[];
}

export interface ChecklistSlot {
  file_type_config_id: number;
  file_type_code: string;
  label: string;
  cycle: string | null;
  state: SlotState;
  files: { id: number; filename: string; processing_status: ProcessingStatus }[];
}

/** GET /dates/<date>/. */
export interface BusinessDateDetail extends BusinessDateOverview {
  checklist: ChecklistSlot[];
  /** Current files of the date in no expected slot. */
  unexpected_files: {
    id: number;
    filename: string;
    processing_status: ProcessingStatus;
    file_type_config_id: number;
    cycle: string | null;
  }[];
  archived: boolean;
  /** Newest first, at most 20. */
  runs: ReconciliationRunInfo[];
  /** Newest first, at most 100. */
  history: OperationLogEntry[];
}

export interface DateFilters {
  date_from?: string;
  date_to?: string;
  /** "-business_date" (default, newest first) or "business_date". */
  ordering?: string;
  page?: number;
  page_size?: number;
}

/** EXISTS: {"detail": "...", "errors": {field: [messages]}} (design 3.4). */
export interface ApiErrorBody {
  detail?: string;
  errors?: Record<string, string[]>;
  /** 400 on approve / bulk approve without confirm_self_approval. */
  self_approval_case_ids?: number[];
}

// ---------------------------------------------------------------------------
// reconciliation results and settlement  (api/results.py, Slice 3; design
// 4.12-4.15). Read-only for all five roles.
// ---------------------------------------------------------------------------
/** RuleEvaluationRun (results._run_payload). */
export interface RuleRunInfo {
  id: number;
  rule_set: string;
  started_at: string;
  finished_at: string | null;
  group_count: number;
  superseded_count: number;
  calendar_incomplete_count: number;
  triggered_by: string;
}

/** GET /dates/<date>/results/summary/ (404 when the date has no results). */
export interface ResultSummary {
  business_date: string;
  rule_evaluation: RuleRunInfo | null;
  /** In the rule set's evaluation order; counts from the latest run. */
  rules: {
    rule_code: string;
    name: string;
    count: number;
    status_category: string | null;
    priority: string | null;
  }[];
  by_status_category: (Choice & { count: number })[];
  by_priority: (Choice & { count: number })[];
  results_total: number;
  match_groups: {
    total: number;
    superseded: number;
    on_us: number;
    cbs_reversed: number;
    by_processing_status: Record<string, number>;
  };
  cases: { total: number; active: number };
}

/** One presence leg of a results row (CBS / NPCI). */
export interface PresenceLeg {
  present: boolean;
  amount: string | null;
  direction: string | null;
  response_code: string | null;
}

/** A transaction case linked to a match group (same module + date + RRN). */
export interface LinkedCase {
  id: number;
  status: string;
  priority: string;
  rule_code: string;
  assigned_to: string | null;
}

/** One row of GET /dates/<date>/results/. */
export interface ResultRow {
  id: number;
  rrn: string;
  business_date: string;
  settlement_cycle: string | null;
  processing_status: string;
  cbs: PresenceLeg;
  npci: PresenceLeg;
  /** Overlay legs present (DRC / TIMEOUT / ADJUSTMENT). */
  overlays: string[];
  amount_match: boolean | null;
  direction_match: boolean | null;
  is_on_us: boolean;
  cbs_reversed: boolean;
  is_superseded: boolean;
  /** The current rule result, or null when the group was not evaluated. */
  rule_result: {
    id: number;
    rule_code: string;
    status_category: string;
    priority: string;
    due_date: string | null;
    due_cutoff_time: string | null;
  } | null;
  case: LinkedCase | null;
}

/** Results list filters (results.ResultListView). Comma lists where noted. */
export interface ResultFilters {
  rule_code?: string;
  status_category?: string;
  priority?: string;
  processing_status?: string;
  rrn?: string;
  presence?: string;
  on_us?: string;
  reversed?: string;
  superseded?: string;
  evaluated?: string;
  /** meta.result_orderings, "-" reverses; default rrn. */
  ordering?: string;
  page?: number;
  page_size?: number;
}

/** A fact row behind a leg (results._fact). */
export interface FactInfo {
  id: number;
  register_type: string;
  business_date: string;
  amount: string | null;
  txn_amount: string | null;
  direction: string;
  settlement_cycle: string | null;
  uploaded_file_id: number | null;
  filename: string | null;
  row_reference: string;
}

export interface GroupLeg {
  leg: string;
  kind: 'presence' | 'overlay';
  present: boolean;
  amount: string | null;
  direction: string | null;
  response_code: string | null;
  ssk_side: string;
  dispute_type: string;
  original_not_loaded: boolean | null;
  fact: FactInfo | null;
  paired_fact: FactInfo | null;
}

/** results._full_rule_result. */
export interface FullRuleResult {
  id: number;
  rule_code: string;
  rule_name: string;
  rule_set: string;
  run_id: number | null;
  status_category: string;
  priority: string;
  action: string;
  response_code: string | null;
  reason: string;
  tat_start_date: string | null;
  tat_label: string;
  tat_unresolved: boolean;
  due_date: string | null;
  due_cutoff_time: string | null;
  calendar_incomplete: boolean;
  rbi_compensation: unknown;
  facts: Record<string, unknown>;
  evaluated_at: string;
  is_superseded: boolean;
  superseded_at: string | null;
}

/** GET /dates/<date>/groups/<id>/. */
export interface GroupDetail {
  id: number;
  rrn: string;
  business_date: string;
  settlement_cycle: string | null;
  processing_status: string;
  flags: {
    amount_match: boolean | null;
    direction_match: boolean | null;
    status_match: boolean | null;
    cbs_reversed: boolean;
    is_on_us: boolean;
    is_superseded: boolean;
  };
  superseded_at: string | null;
  legs: GroupLeg[];
  /** Every DRC / Timeout / Adjustment row of the date for the RRN. */
  overlay_rows: OverlayRow[];
  rule_result: FullRuleResult | null;
  /** Superseded results, newest first. */
  earlier_rule_results: FullRuleResult[];
  case: LinkedCase | null;
  created_at: string;
  updated_at: string;
}

/** An (NTSL, computed, difference) triple of the self-check. */
export interface CheckFigure {
  ntsl: string | null;
  computed: string | null;
  difference: string | null;
}

/** One cycle of the NTSL self-check (results._check_row). */
export interface SelfCheckRow {
  id: number;
  cycle: string;
  status: string;
  failures: unknown;
  map_version: number;
  line_count: number;
  unmapped_count: number;
  unmapped_lines: unknown;
  category_totals: Record<string, unknown>;
  subtotal_debit: CheckFigure;
  subtotal_credit: CheckFigure;
  settlement_net: CheckFigure;
  final_net: CheckFigure;
  checked_at: string;
  source: string;
}

export interface SelfCheck {
  cycles: number;
  pass: number;
  fail: number;
  missing: number;
  all_pass: boolean;
  results: SelfCheckRow[];
}

export type SettlementTransactionKind = 'npci' | 'sop' | 'adjustments' | 'z';

/** The current result on GET /dates/<date>/settlement/. */
export interface SettlementDateResult extends SettlementResultBody {
  map_version: number | null;
  tolerance_version: number | null;
  rule_run_id: number | null;
  source: string;
  unexplained_amount: string | null;
  unexplained_count: number | null;
  transaction_counts: Record<SettlementTransactionKind, number>;
}

/** GET /dates/<date>/settlement/ (404 when neither a result nor a self-check exists). */
export interface SettlementDate {
  business_date: string;
  result: SettlementDateResult | null;
  self_check: SelfCheck;
  case: {
    id: number;
    status: string;
    priority: string;
    needs_review: boolean;
    assigned_to: string | null;
  } | null;
  /** Newest first, at most 20. */
  earlier_results: {
    id: number;
    status: string;
    computed_at: string;
    superseded_at: string | null;
    unexplained_amount: string | null;
  }[];
}

export interface SettlementTransactionFilters {
  kind?: SettlementTransactionKind;
  rrn?: string;
  rule?: string;
  g_rule?: string;
  cycle?: string;
  side?: string;
  page?: number;
  page_size?: number;
}

/** GET .../settlement/transactions/: rows are objects keyed by `fields`. */
export interface SettlementTransactionsPage extends Paginated<Record<string, unknown>> {
  kind: SettlementTransactionKind;
  fields: string[];
  result_id: number | null;
  result_status: string | null;
  counts: Record<SettlementTransactionKind, number>;
}

// ---------------------------------------------------------------------------
// settings and approvals  (api/settings_views.py, Slice 3; design 4.16).
// Reads: all five roles. Writes: Maker proposes codes / mapping,
// Administrator proposes the tolerance, Checker decides (owner 2026-10-20).
// ---------------------------------------------------------------------------
export type SettingsKind = 'response_code' | 'ntsl_mapping' | 'tolerance';

/** Approve body (settings_views.DecisionView). */
export interface SettingsApprovePayload {
  remarks?: string;
  /** Required (server-enforced) when approving your own proposal. */
  confirm_self_approval?: boolean;
}
export interface SettingsRejectPayload {
  reason: string;
}

export interface SettingsEvent {
  event_type: string;
  actor_username: string | null;
  created_at: string;
  remarks: string;
  self_approved: boolean;
  changes?: Record<string, unknown>;
}

/** One row of GET /settings/response-codes/. */
export interface ResponseCodeRow {
  id: number;
  flag: string;
  code: string | null;
  code_known: boolean;
  description: string;
  source: string;
  notes: string;
  label: string;
  /** Used by the active rule set: only an unknown numeric code may be filled in. */
  locked: boolean;
  /** Rule codes using it. */
  used_by: string[];
  pending_request_id: number | null;
  created_at: string;
}

export interface ResponseCodeListPage extends Paginated<ResponseCodeRow> {
  rule_set: string | null;
  can_propose: boolean;
  can_decide: boolean;
}

export interface ResponseCodeFilters {
  /** Comma list of meta.code_flags. */
  flag?: string;
  known?: string;
  page?: number;
  page_size?: number;
}

/** A response-code change request (settings_views.code_request_row + events). */
export interface CodeChangeRequest {
  id: number;
  kind: 'EDIT' | 'CREATE';
  flag: string;
  response_code_id: number | null;
  /** {field: [old, new]}. */
  changes: Record<string, [unknown, unknown]>;
  new_source: string;
  reason: string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED';
  requested_by: string | null;
  requested_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_remarks: string;
  self_approved: boolean;
  /** Pending and proposed by the signed-in user. */
  self_approval: boolean;
  /**
   * The code as shown elsewhere (meta response_codes[].label); null for a
   * CREATE not yet approved (contract D2).
   */
  response_code_label: string | null;
  /** On the request detail (D4) and the pending-approvals "detail"; not on the list. */
  events?: SettingsEvent[];
}

/** GET /settings/response-codes/requests/ (contract D3). */
export interface CodeRequestListPage extends Paginated<CodeChangeRequest> {
  can_propose: boolean;
  can_decide: boolean;
}

export interface CodeRequestFilters {
  status?: string;
  kind?: string;
  page?: number;
  page_size?: number;
}

/** POST /settings/response-codes/requests/ (Maker). Fields left out keep their value. */
export type CodeChangeProposal =
  | {
      kind: 'EDIT';
      response_code: number;
      new_code?: string;
      new_description?: string;
      new_notes?: string;
      reason: string;
    }
  | {
      kind: 'CREATE';
      flag: string;
      new_description: string;
      new_source: string;
      new_code?: string;
      new_notes?: string;
      reason: string;
    };

/** NtslCategoryMapEntry.as_dict. */
export interface MapEntryFields {
  match_type: string;
  pattern: string;
  category: string;
  txn_type: string;
  priority: number;
  notes: string;
}

export interface MapEntry extends MapEntryFields {
  id: number;
  category_label: string;
}

export type VersionStatus = 'PENDING' | 'ACTIVE' | 'RETIRED' | 'REJECTED';

/** settings_views.map_version_row. */
export interface MapVersion {
  id: number;
  version: number;
  status: VersionStatus;
  based_on_version: number | null;
  reason: string;
  proposed_by: string | null;
  proposed_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_remarks: string;
  self_approved: boolean;
  activated_at: string | null;
  retired_at: string | null;
  entry_count: number;
  change_counts: { added: number; removed: number; recategorised_loaded_descriptions: number };
  self_approval: boolean;
}

export interface MapVersionDetail extends MapVersion {
  change_summary: {
    added?: MapEntryFields[];
    removed?: MapEntryFields[];
    recategorised_loaded_descriptions?: { description: string; old: string | null; new: string | null }[];
  } | null;
  events: SettingsEvent[];
}

/** GET /settings/ntsl-mapping/. */
export interface NtslMappingState {
  active: MapVersion | null;
  pending: MapVersion | null;
  categories: Choice[];
  can_propose: boolean;
  can_decide: boolean;
}

export interface MapEntryFilters {
  category?: string;
  match_type?: string;
  search?: string;
  page?: number;
  page_size?: number;
}

/** POST /settings/ntsl-mapping/ (Maker): a new PENDING version = active - removed + added. */
export interface MappingProposal {
  add: Partial<MapEntryFields>[];
  remove_entry_ids: number[];
  reason: string;
}

/** settings_views.tolerance_row. */
export interface ToleranceVersion {
  id: number;
  version: number;
  status: VersionStatus;
  amount: string;
  txn_count: number;
  based_on_version: number | null;
  reason: string;
  proposed_by: string | null;
  proposed_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_remarks: string;
  self_approved: boolean;
  activated_at: string | null;
  retired_at: string | null;
  self_approval: boolean;
}

export interface ToleranceDetail extends ToleranceVersion {
  events: SettingsEvent[];
}

/** Dates whose current settlement used an older tolerance (owner 2026-10-20 Q4: no automatic re-run). */
export interface StaleToleranceDates {
  count: number;
  dates: {
    business_date: string;
    result_id: number;
    settlement_status: string;
    computed_at: string;
    tolerance_version: number | null;
    tolerance: { amount: string | null; txn_count: number | null };
  }[];
  note: string;
}

/** GET /settings/tolerance/. */
export interface ToleranceState {
  active: ToleranceVersion | null;
  /** What applies (and is seeded) on first use when there is no version yet. */
  default_if_none: { amount: string; txn_count: number };
  pending: ToleranceDetail | null;
  can_propose: boolean;
  can_decide: boolean;
  note: string;
  dates_with_older_tolerance: StaleToleranceDates;
}

/** POST /settings/tolerance/ (Administrator). amount is a decimal string. */
export interface ToleranceProposal {
  amount: string;
  txn_count: number;
  reason: string;
}

/** The approve answer for a tolerance also lists the dates on an older one. */
export interface ToleranceDecisionResult extends ToleranceDetail {
  dates_with_older_tolerance?: StaleToleranceDates;
}

/** One item of GET /settings/pending/ (oldest first). */
export interface PendingApproval {
  kind: SettingsKind;
  kind_label: string;
  id: number;
  title: string;
  reason: string;
  proposed_by: string | null;
  proposed_at: string;
  self_approval: boolean;
  detail: CodeChangeRequest | MapVersion | ToleranceVersion;
}

export interface PendingApprovalsPage extends Paginated<PendingApproval> {
  can_decide: boolean;
  cases_awaiting_approval: { count: number; query: Record<string, string> };
}

export interface SettingsDecisionResults {
  response_code: CodeChangeRequest;
  ntsl_mapping: MapVersionDetail;
  tolerance: ToleranceDecisionResult;
}

// ---------------------------------------------------------------------------
// Slice 4: alerts, audit, users and roles
// (docs/phase5_slice4_api_contract.txt, 2026-10-20; design 4.17-4.19)
// ---------------------------------------------------------------------------
/** Contract A: one alert of the signed-in user (core.Notification + read status). */
export interface UserAlert {
  id: number;
  /** R07X_CASE, NEEDS_REVIEW, SUBMITTED, BULK_SUBMITTED, REJECTED, DIGEST_MAKER, ... */
  alert_type: string;
  alert_type_label: string;
  subject: string;
  /** Plain text as mailed. Older alerts carry Django-admin URLs: never link from the body. */
  body: string;
  created_at: string;
  read: boolean;
  read_at: string | null;
  module: string | null;
  /** SPA route ("/upi/cases/123", "/upi/cases?mine=approval"), null if nothing to link to. */
  link: string | null;
  related: { type: 'case'; id: number } | null;
}

export interface AlertFilters {
  /** "true" / "false"; omit for all. */
  unread?: string;
  /** csv */
  alert_type?: string;
  page?: number;
  page_size?: number;
}

/** A1 GET /notifications/. */
export interface AlertListPage extends Paginated<UserAlert> {
  unread: number;
}

/** A2 GET /notifications/unread-count/. */
export interface UnreadAlertCount {
  unread: number;
  /** The newest UNREAD alert's id. */
  latest_id: number | null;
}

/** A4 POST /notifications/read-all/. */
export interface MarkAllReadResult {
  marked: number;
  unread: number;
}

/** Filters shared by the four audit lists (contract B). */
export interface AuditFilters {
  date_from?: string;
  date_to?: string;
  /** Username, exact, case-insensitive. */
  user?: string;
  /** csv */
  event_type?: string;
  ordering?: string;
  page?: number;
  page_size?: number;
  // per list
  module?: string;
  business_date?: string;
  case?: string;
  rrn?: string;
  /** settings-events only, csv */
  kind?: string;
}

/** B1 GET /audit/case-events/. */
export interface AuditCaseEvent {
  id: number;
  created_at: string;
  module: string;
  case_id: number;
  business_date: string;
  rrn: string;
  event_type: string;
  event_type_label: string;
  /** null = the system (case sync). */
  actor_username: string | null;
  source: string;
  old_status: string;
  new_status: string;
  remarks: string;
  changes: Record<string, unknown>;
  action_snapshot: Record<string, unknown> | null;
  self_approved: boolean;
  link: string;
}

/** B2 GET /audit/settings-events/. */
export interface AuditSettingsEvent {
  /** "<kind>:<event id>". */
  id: string;
  kind: SettingsKind;
  kind_label: string;
  event_id: number;
  created_at: string;
  module: string;
  event_type: string;
  event_type_label: string;
  actor_username: string | null;
  remarks: string;
  changes: Record<string, unknown>;
  self_approved: boolean;
  item_id: number;
  item_label: string;
  link: string;
}

/** B3 GET /audit/user-activity/. */
export interface AuditUserActivity {
  id: number;
  created_at: string;
  /** LOGIN / LOGOUT / LOGIN_FAILED. */
  event_type: string;
  event_type_label: string;
  username: string;
  user_id: number | null;
  ip_address: string | null;
  user_agent: string;
  path: string;
}

/** B4 GET /audit/operations/. */
export interface AuditOperation {
  id: number;
  created_at: string;
  module: string;
  event_type: string;
  event_type_label: string;
  username: string | null;
  source: string;
  business_date: string | null;
  uploaded_file_id: number | null;
  run_id: number | null;
  details: Record<string, unknown>;
  link: string | null;
}

/** B5 GET /audit/meta/. */
export interface AuditMeta {
  modules: PaymentModuleRef[];
  case_event_types: Choice[];
  settings_kinds: Choice[];
  settings_event_types: Choice[];
  user_activity_types: Choice[];
  operation_types: Choice[];
  orderings: string[];
}

/** C1 GET /users-and-roles/ row. */
export interface UserRoleRow {
  id: number;
  username: string;
  full_name: string;
  /** In Role order; a superuser holds all five. */
  roles: RoleName[];
  is_superuser: boolean;
  is_active: boolean;
  last_login: string | null;
}

export interface UserRoleFilters {
  role?: string;
  active?: string;
  search?: string;
  page?: number;
  page_size?: number;
}

export interface UserRoleListPage extends Paginated<UserRoleRow> {
  /** "/admin/auth/user/", or null without Django staff status. */
  admin_users_url: string | null;
}
