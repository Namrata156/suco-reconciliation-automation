/**
 * Bulk-bar eligibility (design 4.7). Explains, BEFORE anything is sent, why a
 * selection cannot go in bulk. It mirrors exception_management/services.py
 * (_bulk_cases, bulk_take, bulk_submit, bulk_approve); the server rule still
 * decides and refuses all-or-nothing with a 400.
 *
 * Unlike the server (which stops at the first refusal) every reason is
 * listed, so the user can fix the selection in one go.
 */
import type { ActionTypeMeta, BulkName, CaseListItem } from '../api/types';

export const BULK_PRIORITIES = ['LOW', 'MEDIUM'];
export const PENDING_INVESTIGATION = 'PENDING_INVESTIGATION';

export interface BulkContext {
  /** The signed-in user's username (submit needs cases held by them). */
  username: string;
  /** Bulk submit: the action chosen in the bulk form; "" = the shared pre-fill. */
  actionType?: string;
  actionTypes?: readonly ActionTypeMeta[];
  /** meta.bulk_priorities; defaults to LOW / MEDIUM. */
  bulkPriorities?: readonly string[];
}

export interface BulkEligibility {
  ok: boolean;
  reasons: string[];
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);
const example = (cases: CaseListItem[]) => {
  const c = cases[0];
  if (!c) return '';
  return c.rrn ? ` (e.g. ${c.rrn})` : ` (e.g. ${c.business_date})`;
};
const n = (cases: CaseListItem[]) => `${cases.length} selected ${plural(cases.length, 'case', 'cases')}`;

function settlementReason(cases: CaseListItem[]): string | null {
  const s = cases.filter((c) => c.case_type === 'SETTLEMENT' || c.flags.settlement);
  return s.length
    ? `Settlement cases are never handled in bulk: ${n(s)}${example(s)}. Handle each one individually.`
    : null;
}

function commonReasons(cases: CaseListItem[], allowed: readonly string[]): string[] {
  const out: string[] = [];
  const settlement = settlementReason(cases);
  if (settlement) out.push(settlement);

  const high = cases.filter((c) => !allowed.includes(c.priority));
  if (high.length) {
    const prios = [...new Set(high.map((c) => c.priority))].sort().join(', ');
    out.push(
      `Bulk handling is for ${allowed.join('/')} cases only: ${n(high)} ${plural(high.length, 'is', 'are')} ${prios}${example(high)}.`,
    );
  }

  const pi = cases.filter((c) => c.status_category === PENDING_INVESTIGATION);
  if (pi.length) {
    const rules = [...new Set(pi.map((c) => c.rule_code))].sort().join(', ');
    out.push(
      `Pending Investigation cases (${rules}) are never handled in bulk: ${n(pi)}${example(pi)} must each be handled individually.`,
    );
  }

  const flagged = cases.filter(
    (c) => c.flags.needs_review || c.flags.multiple_adjustment_rows || c.flags.rrc_already_sent,
  );
  if (flagged.length) {
    out.push(
      `${n(flagged)} ${plural(flagged.length, 'carries', 'carry')} a warning (needs review / multiple adjustment rows / RRC already sent)${example(flagged)} and must be handled one by one.`,
    );
  }
  return out;
}

export function bulkEligibility(op: BulkName, cases: CaseListItem[], ctx: BulkContext): BulkEligibility {
  if (cases.length === 0) return { ok: false, reasons: ['No cases selected.'] };
  const reasons: string[] = [];

  if (op === 'take') {
    // Taking only assigns the case: no priority / warning restriction.
    const settlement = settlementReason(cases);
    if (settlement) reasons.push(settlement);
    const notOpen = cases.filter((c) => c.status !== 'OPEN');
    if (notOpen.length) {
      reasons.push(`${n(notOpen)} ${plural(notOpen.length, 'is', 'are')} not Open${example(notOpen)}.`);
    }
  }

  if (op === 'submit') {
    reasons.push(...commonReasons(cases, ctx.bulkPriorities?.length ? ctx.bulkPriorities : BULK_PRIORITIES));
    const notMine = cases.filter((c) => c.status !== 'IN_PROGRESS' || c.assigned_to !== ctx.username);
    if (notMine.length) {
      reasons.push(
        `${n(notMine)} ${plural(notMine.length, 'is', 'are')} not In progress with you${example(notMine)}; take them first.`,
      );
    }
    let action = ctx.actionType || '';
    if (!action) {
      const defaults = [...new Set(cases.map((c) => c.default_action_type || ''))];
      if (defaults.length !== 1 || defaults[0] === '') {
        reasons.push(
          `The selected cases do not share one pre-filled action (${defaults
            .map((d) => d || 'none')
            .sort()
            .join(', ')}); choose the action.`,
        );
      } else {
        action = defaults[0] ?? '';
      }
    }
    const spec = ctx.actionTypes?.find((a) => a.value === action);
    if (spec?.requires_cbs_reversal_evidence) {
      reasons.push(`${spec.label} needs per-case evidence and cannot be submitted in bulk.`);
    }
    if (spec?.settlement_only) {
      reasons.push(`${spec.label} is for settlement cases only.`);
    }
  }

  if (op === 'approve') {
    reasons.push(...commonReasons(cases, ctx.bulkPriorities?.length ? ctx.bulkPriorities : BULK_PRIORITIES));
    const notPending = cases.filter((c) => c.status !== 'PENDING_APPROVAL');
    if (notPending.length) {
      reasons.push(
        `${n(notPending)} ${plural(notPending.length, 'is', 'are')} not pending approval${example(notPending)}.`,
      );
    }
    // "Proposed actions differ" is checked by the server only: list rows do
    // not carry the pending action.
  }

  return { ok: reasons.length === 0, reasons };
}
