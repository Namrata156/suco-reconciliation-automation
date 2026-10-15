import { describe, expect, it } from 'vitest';

import { ACTION_TYPES, makeCase } from '../test/fixtures';
import { bulkEligibility } from './bulk';

const ctx = { username: 'maker1', actionTypes: ACTION_TYPES, bulkPriorities: ['LOW', 'MEDIUM'] };
const mine = (o = {}) => makeCase({ status: 'IN_PROGRESS', assigned_to: 'maker1', ...o });

describe('bulk eligibility (design 4.7)', () => {
  it('allows bulk submit of LOW R01 cases held by me with one shared pre-filled action', () => {
    const r = bulkEligibility('submit', [mine(), mine(), mine()], ctx);
    expect(r).toEqual({ ok: true, reasons: [] });
  });

  it('refuses R13 Pending Investigation cases, naming the rule', () => {
    const r13 = mine({
      rule_code: 'R13',
      status_category: 'PENDING_INVESTIGATION',
      priority: 'MEDIUM',
      rrn: '999',
    });
    const r = bulkEligibility('submit', [mine(), r13], { ...ctx, actionType: 'CLOSE_NO_ACTION' });
    expect(r.ok).toBe(false);
    expect(r.reasons).toHaveLength(1);
    expect(r.reasons[0]).toMatch(/Pending Investigation cases \(R13\) are never handled in bulk/);
    expect(r.reasons[0]).toMatch(/e\.g\. 999/);
  });

  it('refuses R13 for bulk approve too', () => {
    const r13 = makeCase({
      rule_code: 'R13',
      status_category: 'PENDING_INVESTIGATION',
      status: 'PENDING_APPROVAL',
    });
    const r = bulkEligibility('approve', [r13], ctx);
    expect(r.reasons.join(' ')).toMatch(/Pending Investigation/);
  });

  it('does not refuse R13 for bulk take (take only assigns)', () => {
    const r13 = makeCase({ rule_code: 'R13', status_category: 'PENDING_INVESTIGATION' });
    expect(bulkEligibility('take', [r13], ctx).ok).toBe(true);
  });

  it('refuses HIGH priority', () => {
    const r = bulkEligibility('submit', [mine({ priority: 'HIGH' })], ctx);
    expect(r.reasons[0]).toMatch(/LOW\/MEDIUM cases only: 1 selected case is HIGH/);
  });

  it('refuses settlement cases for every bulk action', () => {
    const s = makeCase({ case_type: 'SETTLEMENT', rrn: '', flags: { settlement: true } as never });
    for (const op of ['take', 'submit', 'approve'] as const) {
      expect(bulkEligibility(op, [s], ctx).reasons[0]).toMatch(/Settlement cases are never handled in bulk/);
    }
  });

  it('refuses cases with warning flags', () => {
    const flagged = mine({ flags: { needs_review: true } as never });
    expect(bulkEligibility('submit', [flagged], ctx).reasons[0]).toMatch(/carries a warning/);
  });

  it('asks for an action when pre-filled actions differ, and accepts one chosen', () => {
    const a = mine({ default_action_type: 'DRC_102' });
    const b = mine({ default_action_type: 'RET' });
    expect(bulkEligibility('submit', [a, b], ctx).reasons[0]).toMatch(
      /do not share one pre-filled action \(DRC_102, RET\)/,
    );
    expect(bulkEligibility('submit', [a, b], { ...ctx, actionType: 'CLOSE_NO_ACTION' }).ok).toBe(true);
  });

  it('refuses an evidence action in bulk', () => {
    const r = bulkEligibility('submit', [mine()], { ...ctx, actionType: 'RRC_501_NO_CREDIT' });
    expect(r.reasons[0]).toMatch(/needs per-case evidence/);
  });

  it('requires cases to be in progress with me for submit', () => {
    const r = bulkEligibility('submit', [makeCase({ status: 'OPEN' })], ctx);
    expect(r.reasons[0]).toMatch(/not In progress with you/);
  });

  it('requires OPEN for take and PENDING_APPROVAL for approve', () => {
    expect(bulkEligibility('take', [mine()], ctx).reasons[0]).toMatch(/is not Open/);
    expect(bulkEligibility('approve', [makeCase()], ctx).reasons[0]).toMatch(/not pending approval/);
  });

  it('lists every reason, not only the first', () => {
    const c = makeCase({ priority: 'HIGH', flags: { rrc_already_sent: true } as never });
    expect(bulkEligibility('approve', [c], ctx).reasons).toHaveLength(3);
  });
});
