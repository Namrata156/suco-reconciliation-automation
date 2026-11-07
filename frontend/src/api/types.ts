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
  /** Read the users-and-roles list (Administrator, Auditor; owner 2026-10-22). */
  'users.view': boolean;
  /** The Django admin link (Administrator only). */
  'users.admin': boolean;
  /** NPCI file list and details (all five; docs/npci_file_design_draft_v1.txt A7). */
  'npci_files.view': boolean;
  /** Generate the next NPCI file of a date (Checker). */
  'npci_files.generate': boolean;
  /** Download a generated file (Maker, Checker, Operations User). */
  'npci_files.download': boolean;
  // Phase 6 (docs/phase6_api_contract.txt C1): NOT sent by the server yet.
  // Optional until the backend adds them; lib/roles.ts allows() falls back
  // to existing capabilities while a key is absent.
  /** Reports page and the all-roles reports (all five). */
  'reports.view'?: boolean;
  /** Approval report (Administrator, Auditor, Checker; R2). */
  'reports.approvals'?: boolean;
  /** User activity report (Administrator, Auditor; R2). */
  'reports.user_activity'?: boolean;
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

// ---------------------------------------------------------------------------
// UI refinements U2 / U3 (docs/ui_refinements_api_contract.txt, 2026-10-27).
// Every field is an addition; optional here so an older server (or a test
// fixture) without it still type-checks and the screen falls back.
// ---------------------------------------------------------------------------
/** A0: one plain status per business date. Values may grow (FILE_PENDING): show the server's label. */
export type DateStatus =
  | 'RUNNING'
  | 'WAITING_FOR_FILES'
  | 'READY_TO_RUN'
  | 'CASES_OPEN'
  | 'FILE_PENDING'
  | 'DONE'
  | (string & {});

/** One SSKUPI file of a date (A1a / A2). */
export interface SskupiFileBrief {
  id: number;
  file_name: string;
  sequence: number;
  row_count: number;
  generated_at: string;
  generated_by: string;
  download_count: number;
  last_download: { by: string; at: string } | null;
}

/** NONE | GENERATED | DOWNLOADED (of the date's latest file). */
export type SskupiState = 'NONE' | 'GENERATED' | 'DOWNLOADED' | (string & {});

/** A2: compact SSKUPI block per date row (null: module without an NPCI format). */
export interface SskupiCompact {
  state: SskupiState;
  state_label: string;
  file_count: number;
  latest: SskupiFileBrief | null;
  /** Files of the date never downloaded (any sequence); FILE_PENDING until they are. */
  not_downloaded?: string[];
}

/** A1a: the full SSKUPI block of one date (home page). */
export interface SskupiFull extends SskupiCompact {
  files: SskupiFileBrief[];
  /** Rows the NEXT file would hold now. */
  waiting_rows: number;
  next_file_name: string;
  /** Left out until a Checker acts (NEEDS_APPROVAL + NEEDS_REVIEW). */
  held_for_checker: number;
  left_out: number;
  changed_since_included: number;
  can_generate: boolean;
}

/** A1: the home page's "Today's job" for the summary's business date (all five roles). */
export interface DayBlock {
  business_date: string;
  status: DateStatus;
  status_label: string;
  status_detail: string;
  files: {
    expected: number;
    present: number;
    missing: number;
    loaded: number;
    processing: number;
    failed: number;
    awaiting_date_selection: number;
  };
  run: {
    state: 'NOT_RUN' | 'RUNNING' | 'DONE' | 'FAILED' | (string & {});
    run_id: number | null;
    requested_by: string | null;
    finished_at: string | null;
    error: string;
  };
  results: {
    reconciled_at: string | null;
    rule_set: string;
    transactions: number;
    matched: number;
    exceptions: number;
  } | null;
  settlement: { status: string; status_label: string; unexplained_amount: string | null } | null;
  cases: { open: number; total: number };
  sskupi: SskupiFull | null;
  /** SPA address of the date page. */
  link: string;
}

export interface ModuleSummary {
  /** ISO date (IST): today's calendar date, when the counts were taken. Not the business date. */
  business_date_today: string;
  /**
   * ISO date (IST): the latest business date that can be complete (today
   * minus one; ops reconcile the previous day). Being added by the backend;
   * absent from older servers (the SPA then uses business_date_today - 1).
   */
  business_date?: string;
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
  /** U2 1.1 / 1.2: the summary business date's day, to every role. */
  day?: DayBlock;
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

/** The six queue tabs (A3); every case is in exactly one. */
export type ReplyType = 'DRC' | 'TCC' | 'RET' | 'RRC' | 'NEEDS_REVIEW' | 'SETTLEMENT';

/** The NPCI reply a case carries now (contract "reply" object, generator precedence). */
export interface CaseReply {
  reply_type: ReplyType | (string & {});
  reply_type_label: string;
  action_type: string;
  action_type_label: string;
  flag: string | null;
  code: string | null;
  text: string | null;
  /** Ready-made button / column text ("DRC 102 - Online Reversal"); null when nothing is chosen. */
  label: string | null;
  source: 'RULE' | 'SUBMITTED' | 'APPROVED' | (string & {});
  source_label: string;
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
  // --- U2 (A3) -------------------------------------------------------------
  amount?: string | null;
  /** Which row the amount came from (DRC / TIMEOUT / ADJUSTMENT / NPCI / CBS / SETTLEMENT). */
  amount_source?: string | null;
  /** "" for a settlement case. */
  rule_name?: string;
  reply?: CaseReply | null;
  /** The SSKUPI file the case is already in. */
  npci_file?: { id: number; file_name: string } | null;
}

/** GET /cases/: the page plus the reply-type tab counts (A3, without the reply_type filter). */
export interface CaseListPage extends Paginated<CaseListItem> {
  reply_type_counts?: Partial<Record<ReplyType, number>>;
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
  /** A4: null for a settlement case. */
  what_happened?: WhatHappened | null;
}

/** A4: CBS vs NPCI, the reply and where the case stands for the SSKUPI file. */
export interface WhatHappened {
  cbs: { present: boolean; amount: string | null; direction: string | null };
  npci: {
    present: boolean;
    amount: string | null;
    direction: string | null;
    response_code: string | null;
    /** APPROVED (code 00 / RB) | DECLINED | null. */
    status: string | null;
  };
  reply: CaseReply | null;
  sskupi: {
    /** IN_FILE | NOT_YET_IN_FILE | HELD_FOR_CHECKER | LEFT_OUT | NO_FILE_REPLY (show the label). */
    status: string;
    status_label: string;
    reason: string | null;
    reason_label: string | null;
    detail: string;
    file_date: string | null;
    file: {
      id: number;
      file_name: string;
      file_date: string;
      line: number;
      generated_at: string;
      generated_by: string;
      flag: string;
      code: string;
      reply_source: string;
    } | null;
    changed_since_included: boolean;
  };
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
  /** csv of ReplyType (A3). */
  reply_type?: string;
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

/** U4 E1: one expected file type x cycle of the date, against this batch. */
export interface DetectChecklistSlot {
  file_type_config_id: number;
  file_type_code: string;
  label: string;
  cycle: string | null;
  /** The slot's state now (date overview states). */
  state: SlotState;
  /** ALREADY_PRESENT | IN_BATCH | MISSING (still missing after this batch). */
  group: 'ALREADY_PRESENT' | 'IN_BATCH' | 'MISSING' | (string & {});
  /** Indexes of this batch's files proposed for the slot (on ALREADY_PRESENT: they will be refused). */
  batch_files: number[];
  existing_files: { id: number; filename: string; processing_status: ProcessingStatus }[];
}

/** U4 E1: expected vs. in this batch vs. still missing, for the chosen date. */
export interface DetectChecklist {
  business_date: string;
  summary: {
    expected: number;
    already_present: number;
    in_batch: number;
    missing_after_batch: number;
    outside_checklist: number;
  };
  slots: DetectChecklistSlot[];
  /** Indexes of batch files that fill no slot of the date. */
  outside_checklist: number[];
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
  /** U4 2.1 (E1): null when there is no date (none given, none in the names). */
  checklist?: DetectChecklist | null;
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
  /** B2: whether the stored original can be downloaded, and why not. */
  original?: {
    available: boolean;
    reason: 'NOT_STORED' | 'MISSING' | 'CHANGED' | (string & {}) | null;
    message: string;
    download_url: string | null;
  };
  /** Newest first. */
  downloads?: { by: string; at: string }[];
  download_count?: number;
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
  /** csv of file ids (U4 E2: the upload page polls the files it just sent). */
  ids?: string;
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
  // --- U2 (A0 / A2) ----------------------------------------------------------
  status?: DateStatus;
  status_label?: string;
  status_detail?: string;
  sskupi?: SskupiCompact | null;
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
  /** Fee GST check (settlement.fee_gst, owner 2026-11-06): warning only, never changes status. */
  gst_warning: boolean;
  /** null: not checked (stored before the check existed); {}: MISSING cycle. */
  gst_check: GstCheck | Record<string, never> | null;
  checked_at: string;
  source: string;
}

export type GstCheckNote = 'OUTSIDE_TOLERANCE' | 'GST_WITHOUT_FEE' | 'FEE_WITHOUT_GST';

/** One fee / GST pair on one side (results._check_row; amounts are 2-decimal strings). */
export interface GstCheckRow {
  fee_category: string;
  gst_category: string;
  side: 'DEBIT' | 'CREDIT';
  fee: string;
  /** Information only (both sides together). */
  fee_txn_count: number;
  expected_gst: string;
  ntsl_gst: string;
  /** expected - NTSL. */
  difference: string;
  tolerance: string;
  within: boolean;
  note: GstCheckNote | null;
}

/** The fee GST check of one cycle: always 12 rows (6 pairs x DEBIT / CREDIT). */
export interface GstCheck {
  rate: string;
  tolerance: string;
  rows: GstCheckRow[];
}

export interface SelfCheck {
  cycles: number;
  pass: number;
  fail: number;
  missing: number;
  all_pass: boolean;
  /** Cycles with a GST warning; NOT part of all_pass. */
  gst_warnings: number;
  results: SelfCheckRow[];
}

export type SettlementTransactionKind = 'npci' | 'sop' | 'adjustments' | 'z' | 'unexplained';

/** The current result on GET /dates/<date>/settlement/. */
export interface SettlementDateResult extends SettlementResultBody {
  map_version: number | null;
  tolerance_version: number | null;
  rule_run_id: number | null;
  source: string;
  unexplained_amount: string | null;
  unexplained_count: number | null;
  transaction_counts: Partial<Record<SettlementTransactionKind, number>>;
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
  /** B3: the plain summary (null when there is no settlement result). */
  summary?: SettlementSummary | null;
}

/** One type of the "explained" breakdown (B3). */
export interface ExplainedType {
  type: string;
  label: string;
  count: number;
  amount: string | null;
  /** False for the late adjustments (they explain NTSL's net adjusted amount, not the SOP difference). */
  in_sop_check: boolean;
  rules: { rule: string; g_rule: string; g_label: string; count: number; amount: string | null }[];
}

/** B3 GET /dates/<date>/settlement/ "summary". */
export interface SettlementSummary {
  status: string;
  status_label: string;
  tallied: boolean;
  reasons: string[];
  cbs_net: string | null;
  ntsl_net: string | null;
  difference: string | null;
  explained: string | null;
  unexplained: string | null;
  cycles_unexplained_amount: string | null;
  cycles_unexplained_count: number | null;
  unexplained_entries: number;
  explained_by_type: ExplainedType[];
  cbs_rows_without_valid_rrn: { count: number; amount: string | null } | null;
  self_check_failed: boolean;
  settlement_case: { id: number; status: string; link: string } | null;
  /**
   * Notes shown next to the tally ({} today; owner Q3 may move the late
   * adjustments here as {"late_adjustments": {label, count, amount}}).
   */
  notes?: Record<string, unknown>;
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
  counts: Partial<Record<SettlementTransactionKind, number>>;
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

/** A response-code field an edit proposal may change. */
export type ResponseCodeField = 'code' | 'description' | 'notes';

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
  /**
   * Used by the active rule set: its flag, a known numeric code, its notes and
   * its rule links stay locked (see editable_fields).
   */
  locked: boolean;
  /**
   * What an edit proposal may change (NPCI file design Amendment 1, A7):
   * ["code", "description", "notes"] on an unlocked code; ["description"] on a
   * locked one, or ["code", "description"] while its numeric code is unknown.
   */
  editable_fields: ResponseCodeField[];
  /** Rule codes using it. */
  used_by: string[];
  pending_request_id: number | null;
  created_at: string;
  // --- U2 7.2 (A6) ---------------------------------------------------------
  /** DRC | TCC | RET | RRC | OTHER (NPCI response codes). */
  reply_type?: string;
  in_sskupi_file?: boolean;
  /** The exact SSKUPI "specifyother" text; null when not an SSKUPI reply. */
  sskupi_text?: string | null;
  /** In the SSKUPI file and the numeric code is known. */
  sskupi_ready?: boolean;
  /** Why a reply with this text would be left out of the file, or null. */
  sskupi_problem?: string | null;
}

export interface ResponseCodeListPage extends Paginated<ResponseCodeRow> {
  rule_set: string | null;
  can_propose: boolean;
  can_decide: boolean;
  /** A6: the groups in SSKUPI file order (DRC, RRC, TCC, RET, then OTHER), whole table. */
  reply_types?: (Choice & { count: number })[];
}

export interface ResponseCodeFilters {
  /** Comma list of meta.code_flags. */
  flag?: string;
  known?: string;
  /** "reply_type": SSKUPI file order, then code (A6). */
  ordering?: string;
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
  /** One code's requests (meta response_codes[].id). */
  response_code?: number;
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
  /** csv (as kept in the address), or a list the client joins; values from AlertListPage.alert_types. */
  alert_type?: string | string[];
  page?: number;
  page_size?: number;
}

/** A1 GET /notifications/. */
export interface AlertListPage extends Paginated<UserAlert> {
  unread: number;
  /** The values ?alert_type= accepts, with labels, for the filter (owner 2026-10-22). */
  alert_types: Choice[];
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
  /** everything only (B5), csv of case_event / settings_event / user_activity / operation. */
  source?: string;
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
  /** B5: the "Everything" source filter choices. */
  everything_sources?: Choice[];
}

/** U5 F1: one row of GET /reports/npci-files/ (the NPCI file row plus its date and links). */
export interface NpciFilesReportRow extends NpciFileRow {
  business_date: string;
  links: { file: string; date: string };
}

export interface NpciFilesReportFilters {
  date_from?: string;
  date_to?: string;
  /** "true" | "false"; omit = all. */
  downloaded?: string;
  /** "-business_date" (default) | "business_date". */
  ordering?: string;
  page?: number;
  page_size?: number;
}

/** U5 F1 GET /reports/npci-files/. */
export interface NpciFilesReport extends Paginated<NpciFilesReportRow> {
  /** The whole range, not the page. */
  totals: { files: number; rows: number; amount: string; downloaded: number; not_downloaded: number };
  report: {
    key: string;
    title: string;
    module: string;
    filters: { date_from: string; date_to: string; downloaded: boolean | null; ordering: string };
    generated_at: string;
    generated_by: string;
  };
  /** Excel / CSV comes with the shared Phase 6 export; null until then. */
  export: unknown;
}

/** U5 F2 GET /dashboard/: one business date. */
export interface DashboardDate {
  business_date: string;
  status: DateStatus;
  status_label: string;
  volume: {
    transactions: number;
    npci: { count: number; amount: string | null };
    cbs: { count: number; amount: string | null };
  };
  exceptions: Partial<Record<ReplyType, number>> & { total: number };
  open_cases: number;
  settlement: { status: string; tallied: boolean; unexplained_amount: string | null } | null;
  sskupi: { state: string; file_count: number; not_downloaded: number; pending: boolean } | null;
  link: string;
}

/** U5 F2 GET /dashboard/ (plain numbers per date, oldest first; charts come later). */
export interface Dashboard {
  range: { date_from: string; date_to: string; dates: number };
  reply_types: Choice[];
  dates: DashboardDate[];
  open_cases_by_age: {
    buckets: { label: string; min_days: number; max_days: number | null; count: number; overdue: number }[];
    total: number;
    overdue: number;
  };
  generated_at: string;
}

/** B5 GET /audit/everything/: one row of the merged timeline. */
export interface AuditEverythingRow {
  /** Unique across sources ("case_event:9876", "settings_event:<kind>:<id>"). */
  id: string;
  source: 'case_event' | 'settings_event' | 'user_activity' | 'operation' | (string & {});
  source_label: string;
  event_id: number;
  created_at: string;
  module: string | null;
  /** null = the system. */
  username: string | null;
  event_type: string;
  event_type_label: string;
  /** One plain sentence, made by the server. */
  summary: string;
  link: string | null;
  /** The row exactly as the source's own list returns it. */
  details: Record<string, unknown>;
}

/** B1 GET /<module>/rrn/<rrn>/ (200 even when nothing is found). */
export interface RrnLookup {
  rrn: string;
  found: boolean;
  transactions: {
    id: number;
    business_date: string;
    is_superseded: boolean;
    processing_status: string;
    rule_code: string | null;
    rule_name: string | null;
    cbs: { present: boolean; amount: string | null; direction: string | null };
    npci: { present: boolean; amount: string | null; direction: string | null; response_code: string | null };
    link: string;
  }[];
  cases: {
    id: number;
    business_date: string;
    case_type: string;
    status: string;
    rule_code: string;
    rule_name: string;
    reply_label: string | null;
    link: string;
  }[];
  file_rows: {
    register_type: string;
    business_date: string;
    uploaded_file_id: number;
    filename: string;
    amount: string | null;
    direction: string | null;
    link: string;
  }[];
  /** Replaced transactions not listed (include_replaced=false). */
  replaced_count: number;
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

// ---------------------------------------------------------------------------
// NPCI file generation (api/npci_files.py; docs/npci_file_design_draft_v1.txt
// Amendments 1 and 2, A7). The app only GENERATES the file; the user downloads
// it and uploads it to the NPCI portal by hand (owner 2026-10-22/23). Since
// Amendment 2 (owner 2026-10-24) the file uses each case's current reply
// (Checker-approved, else the Maker's submitted one, else the rule's
// suggestion). Account numbers are masked (last 4 only).
// ---------------------------------------------------------------------------
/**
 * Amendment 2: OPEN and PENDING_APPROVAL are gone, but files generated before
 * 2026-10-24 may still hold them in their stored not_included (shown with
 * their stored reason_label).
 */
export type NpciNotIncludedReason =
  'NEEDS_REVIEW' | 'NEEDS_APPROVAL' | 'REJECTED' | 'NO_NUMERIC_CODE' | 'NO_NPCI_ROW' | 'BAD_SOURCE_DATA';

/** Where a row's reply came from (Amendment 2). */
export type NpciReplySource = 'RULE' | 'SUBMITTED' | 'APPROVED';

/** Rows and amount per flag / code / text. */
export interface NpciFileCount {
  flag: string;
  code: string;
  text: string;
  rows: number;
  amount: string;
}

/** One line of a file (or of the next file, in the preview). */
export interface NpciFileLine {
  line: number;
  case_id: number;
  /** An RRC case can belong to another date's file (its Adjustment's Adjdate). */
  case_business_date: string;
  rrn: string;
  flag: string;
  code: string;
  /** YYYY-MM-DD. */
  shtdat: string;
  /** As written in the file ("140.5"). */
  amount: string;
  account_masked: string;
  text: string;
  /**
   * Amendment 2: RULE, SUBMITTED or APPROVED. Rows of files generated before
   * 2026-10-24 say APPROVED. Optional until every server sends it.
   */
  reply_source?: NpciReplySource | string;
  /** Amendment 2 on file-detail rows; always on preview rows. */
  action_type?: string;
}

export interface NpciPreviewLine extends NpciFileLine {
  rule_code: string;
  action_type: string;
}

/** A case that would give a row once decided, and why it is left out. */
export interface NpciNotIncluded {
  case_id: number;
  case_business_date: string;
  rrn: string;
  rule_code: string;
  status: string;
  flag: string;
  /** null when the reply code has no numeric NPCI code yet. */
  code: string | null;
  reason: NpciNotIncludedReason | string;
  reason_label: string;
  detail: string;
}

/** preview.files / detail.other_files_for_date. */
export interface NpciFileRef {
  id: number;
  file_name: string;
  sequence: number;
  row_count: number;
  partial: boolean;
  generated_at: string;
  generated_by: string;
}

/** GET /dates/<date>/npci-file/preview/: what the NEXT file would hold now. */
export interface NpciFilePreview {
  file_date: string;
  file_format: string;
  next_sequence: number;
  next_file_name: string;
  row_count: number;
  total_amount: string;
  partial: boolean;
  counts: NpciFileCount[];
  rows: NpciPreviewLine[];
  not_included: NpciNotIncluded[];
  not_included_counts: Partial<Record<NpciNotIncludedReason | string, number>>;
  /** Cases of this date already included in a file. */
  already_sent_count: number;
  already_sent_files: string[];
  /** Amendment 2: rows of the next file per reply source (only sources present). */
  reply_sources?: Partial<Record<NpciReplySource | string, number>>;
  /**
   * Amendment 2: cases already in a file of this date that changed since
   * (flagged for review, another reply chosen, or closed by a re-run).
   */
  changed_since_included_count?: number;
  /** Shown as they are. */
  warnings: string[];
  files: NpciFileRef[];
  /** Rows > 0 and the user is a Checker. */
  can_generate: boolean;
}

export interface NpciGeneratePayload {
  confirm_partial?: boolean;
  remarks?: string;
}

/** One row of GET /npci-files/. */
export interface NpciFileRow {
  id: number;
  file_date: string;
  sequence: number;
  file_name: string;
  file_format: string;
  row_count: number;
  total_amount: string;
  partial: boolean;
  not_included_count: number;
  sha256: string;
  size_bytes: number;
  generated_by: string;
  generated_at: string;
  remarks: string;
  /** null when the user may not download (Administrator, Auditor). */
  download_url: string | null;
  /**
   * Who downloaded it and when, newest first (owner 2026-10-23; A7 update).
   * Optional until the backend sends it.
   */
  downloads?: NpciFileDownload[];
  download_count?: number;
}

/** One download of a generated file. */
export interface NpciFileDownload {
  /** Username. */
  by: string;
  at: string;
}

export interface NpciFileListPage extends Paginated<NpciFileRow> {
  can_download: boolean;
}

export interface NpciFileFilters {
  file_date?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}

/** GET /npci-files/<id>/ (also the body of a 201 generate). */
export interface NpciFileDetail extends NpciFileRow {
  counts: NpciFileCount[];
  warnings: string[];
  /** As they stood at generation. */
  not_included: NpciNotIncluded[];
  rows: NpciFileLine[];
  other_files_for_date: NpciFileRef[];
  /** NPCI_FILE_GENERATED and NPCI_FILE_DOWNLOADED entries, oldest first. */
  history: OperationLogEntry[];
}
