/**
 * Approve / reject a settings proposal (design 4.16; owner 2026-10-20):
 * Checkers decide; approving your own proposal needs the same explicit
 * confirmation as a case approval (confirm_self_approval, server-enforced).
 * A 409 (someone decided first) closes the dialog, says so and reloads.
 */
import { Modal } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useQueryClient } from '@tanstack/react-query';

import { ApiError, decideSetting } from '../../api/client';
import type { SettingsDecisionResults, SettingsKind } from '../../api/types';
import { ApproveForm, RejectForm } from '../cases/actionForms';

/** settings_views.SELF_APPROVAL_MESSAGE. */
export const SETTINGS_SELF_APPROVAL_TEXT =
  'You proposed this change; approving it will be recorded as self-approved.';

export interface DecisionTarget {
  kind: SettingsKind;
  id: number;
  /** "Tolerance v2: Rs 1.00 / 0 txn" - shown in the dialog. */
  title: string;
  /** The signed-in user proposed it (approving = self-approval). */
  selfApproval: boolean;
  decision: 'approve' | 'reject';
}

/** Everything a settings write changes on other screens. */
export function invalidateSettings(qc: ReturnType<typeof useQueryClient>, module: string) {
  void qc.invalidateQueries({ queryKey: ['settings', module] });
  void qc.invalidateQueries({ queryKey: ['summary', module] });
  void qc.invalidateQueries({ queryKey: ['meta', module] });
}

export function DecisionModal({
  module,
  target,
  onClose,
  onDone,
}: {
  module: string;
  target: DecisionTarget | null;
  onClose: () => void;
  onDone?: (target: DecisionTarget, result: SettingsDecisionResults[SettingsKind]) => void;
}) {
  const qc = useQueryClient();

  const run = async (payload: { remarks?: string; confirm_self_approval?: boolean } | { reason: string }) => {
    if (!target) return;
    try {
      const result =
        target.decision === 'approve'
          ? await decideSetting(module, target.kind, target.id, 'approve', payload as { remarks?: string })
          : await decideSetting(module, target.kind, target.id, 'reject', payload as { reason: string });
      notifications.show({
        color: 'green',
        title: target.decision === 'approve' ? 'Change approved' : 'Change rejected',
        message: target.title,
      });
      invalidateSettings(qc, module);
      onDone?.(target, result);
      onClose();
    } catch (e) {
      if (e instanceof ApiError && e.isConflict) {
        notifications.show({
          color: 'orange',
          title: 'The proposal changed',
          message: `${e.detail} The list has been reloaded.`,
          autoClose: 10_000,
        });
        invalidateSettings(qc, module);
        onClose();
        return;
      }
      throw e;
    }
  };

  const verb = target?.decision === 'approve' ? 'Approve' : 'Reject';
  return (
    <Modal opened={target !== null} onClose={onClose} title={target ? `${verb} ${target.title}` : ''}>
      {target?.decision === 'approve' && (
        <ApproveForm
          selfApproval={target.selfApproval}
          selfApprovalMessage={SETTINGS_SELF_APPROVAL_TEXT}
          confirmLabel="I confirm this approval of my own proposal"
          actionSummary={target.title}
          onCancel={onClose}
          onSubmit={run}
        />
      )}
      {target?.decision === 'reject' && (
        <RejectForm
          intro="The proposal is rejected with this reason; nothing changes."
          onCancel={onClose}
          onSubmit={run}
        />
      )}
    </Modal>
  );
}
