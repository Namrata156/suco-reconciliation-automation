import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../api/client';
import { META, renderWithProviders } from '../../test/fixtures';
import {
  ApproveForm,
  CompleteForm,
  RejectForm,
  SELF_APPROVAL_TEXT,
  SubmitActionForm,
  submitSchema,
} from './actionForms';

const caseOf = (o = {}) => ({
  default_action_type: 'DRC_102',
  default_response_code: 'DRC 102 - Reversed online',
  case_type: 'TRANSACTION' as const,
  rrc_already_sent_on: null,
  rule_code: 'R01',
  ...o,
});

describe('SubmitActionForm (design 4.6)', () => {
  it('pre-fills the rule action (R01 -> DRC 102) and submits it', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(
      <SubmitActionForm caseDetail={caseOf()} meta={META} onSubmit={onSubmit} onCancel={() => {}} />,
    );
    expect(screen.getByText(/Sent as DRC 102/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Submit for approval' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ action_type: 'DRC_102' }));
  });

  it('requires a choice when the rule has no default action (R13)', async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <SubmitActionForm
        caseDetail={caseOf({ default_action_type: '', default_response_code: null, rule_code: 'R13' })}
        meta={META}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText(/Rule R13 has no pre-filled action/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Submit for approval' }));
    expect(await screen.findByText('Choose an action (this rule has no default)')).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('shows and requires the CBS reversal evidence for RRC 501 without a new credit', async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <SubmitActionForm
        caseDetail={caseOf({
          default_action_type: 'RRC_501_NO_CREDIT',
          default_response_code: null,
          rule_code: 'R06b',
        })}
        meta={META}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );
    expect(screen.getByText('CBS reversal already posted (evidence, required)')).toBeTruthy();
    await userEvent.type(screen.getByLabelText(/CBS reversal amount/), '12.345');
    await userEvent.click(screen.getByRole('button', { name: 'Submit for approval' }));
    expect(await screen.findByText('The CBS reversal date is required')).toBeTruthy();
    expect(screen.getByText(/positive amount with at most 2 decimals/)).toBeTruthy();
    expect(screen.getByText('The CBS reversal RRN is required')).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('does not show evidence fields for other actions', () => {
    renderWithProviders(
      <SubmitActionForm caseDetail={caseOf()} meta={META} onSubmit={vi.fn()} onCancel={() => {}} />,
    );
    expect(screen.queryByText('CBS reversal already posted (evidence, required)')).toBeNull();
  });

  it('pre-fills the RRC-already-sent warning as evidence notes', () => {
    renderWithProviders(
      <SubmitActionForm
        caseDetail={caseOf({ rrc_already_sent_on: '2026-07-10' })}
        meta={META}
        onSubmit={vi.fn()}
        onCancel={() => {}}
      />,
    );
    const notes = screen.getByLabelText(/Evidence notes/) as HTMLTextAreaElement;
    expect(notes.value).toBe('RRC already sent for this RRN on 2026-07-10; check before crediting');
  });

  it('requires the explanation on a settlement case', async () => {
    const onSubmit = vi.fn();
    renderWithProviders(
      <SubmitActionForm
        caseDetail={caseOf({
          case_type: 'SETTLEMENT',
          default_action_type: '',
          default_response_code: null,
          rule_code: '',
        })}
        meta={META}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Submit for approval' }));
    expect(await screen.findByText('The explanation is required')).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('schema: RET needs a reason code unless the rule default applies', () => {
    const base = {
      response_code: '',
      remarks: '',
      evidence_cbs_reversal_date: '',
      evidence_cbs_reversal_amount: '',
      evidence_cbs_reversal_rrn: '',
      evidence_notes: '',
    };
    const ctx = {
      isSettlement: false,
      defaultActionType: 'RET',
      defaultResponseCode: 'RET 115',
      actionTypes: META.action_types,
    };
    expect(submitSchema(ctx).safeParse({ ...base, action_type: 'RET' }).success).toBe(true);
    const noDefault = { ...ctx, defaultResponseCode: null };
    expect(submitSchema(noDefault).safeParse({ ...base, action_type: 'RET' }).success).toBe(false);
    expect(
      submitSchema(noDefault).safeParse({ ...base, action_type: 'RET', response_code: '11' }).success,
    ).toBe(true);
  });

  it('shows server field errors in the form', async () => {
    const onSubmit = vi
      .fn()
      .mockRejectedValue(
        new ApiError(400, { detail: 'Invalid input', errors: { remarks: ['Too long.'] } }, 'x'),
      );
    renderWithProviders(
      <SubmitActionForm caseDetail={caseOf()} meta={META} onSubmit={onSubmit} onCancel={() => {}} />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Submit for approval' }));
    expect(await screen.findByText('Too long.')).toBeTruthy();
  });
});

describe('ApproveForm self-approval (design 4.6 / Phase 3 P4)', () => {
  it('asks for confirmation and sends confirm_self_approval', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<ApproveForm selfApproval onSubmit={onSubmit} onCancel={() => {}} />);
    expect(screen.getByText(SELF_APPROVAL_TEXT)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Approve (self-approved)' }));
    expect(await screen.findByText('Confirm the self-approval to continue')).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
    await userEvent.click(screen.getByLabelText(/I confirm this approval of my own submission/));
    await userEvent.click(screen.getByRole('button', { name: 'Approve (self-approved)' }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ remarks: undefined, confirm_self_approval: true }),
    );
  });

  it('a plain approval needs no confirmation', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<ApproveForm selfApproval={false} onSubmit={onSubmit} onCancel={() => {}} />);
    expect(screen.queryByText(SELF_APPROVAL_TEXT)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ remarks: undefined }));
  });

  it('switches to the confirmation when the server asks for it', async () => {
    const onSubmit = vi
      .fn()
      .mockRejectedValueOnce(
        new ApiError(
          400,
          {
            detail: SELF_APPROVAL_TEXT,
            errors: { confirm_self_approval: ['Confirm.'] },
            self_approval_case_ids: [5],
          },
          'x',
        ),
      )
      .mockResolvedValue(undefined);
    renderWithProviders(<ApproveForm selfApproval={false} onSubmit={onSubmit} onCancel={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText(SELF_APPROVAL_TEXT)).toBeTruthy();
    await userEvent.click(screen.getByLabelText(/I confirm this approval/));
    await userEvent.click(screen.getByRole('button', { name: 'Approve (self-approved)' }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenLastCalledWith({ remarks: undefined, confirm_self_approval: true }),
    );
  });
});

describe('RejectForm / CompleteForm', () => {
  it('reject needs a reason', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<RejectForm onSubmit={onSubmit} onCancel={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));
    expect(await screen.findByText('A rejection reason is required')).toBeTruthy();
    await userEvent.type(screen.getByLabelText(/Reason/), '  wrong code  ');
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith({ reason: 'wrong code' }));
  });

  it('mark done needs the NPCI portal / CBS reference', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<CompleteForm onSubmit={onSubmit} onCancel={() => {}} />);
    await userEvent.click(screen.getByRole('button', { name: 'Mark done' }));
    expect(await screen.findByText('The NPCI portal / CBS reference is required')).toBeTruthy();
    await userEvent.type(screen.getByLabelText(/NPCI portal \/ CBS reference/), 'NPCI-123');
    await userEvent.click(screen.getByRole('button', { name: 'Mark done' }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ external_reference: 'NPCI-123', remarks: undefined }),
    );
  });
});
