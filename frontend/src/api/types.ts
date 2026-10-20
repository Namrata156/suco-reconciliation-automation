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

export interface SettlementInfo {
  result_id: number;
  first_result_id: number;
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
