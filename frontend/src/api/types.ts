/**
 * API shapes for /api/v1/ (design docs/phase5_design_draft_v1.txt, section 3/4).
 *
 * Reconciled 2026-10-15 against the built backend: api/auth.py, api/base.py,
 * api/views.py (v1) and exception_management/api.py (the serializers v1
 * extends). Every API call lives in ./client.ts, so a changed shape is fixed
 * in these two files.
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
    /** Null until Slice 2 builds the expected-files checklist. */
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

/** EXISTS: {"detail": "...", "errors": {field: [messages]}} (design 3.4). */
export interface ApiErrorBody {
  detail?: string;
  errors?: Record<string, string[]>;
  /** 400 on approve / bulk approve without confirm_self_approval. */
  self_approval_case_ids?: number[];
}
