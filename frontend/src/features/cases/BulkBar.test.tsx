import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { makeCase, makeMe, META, permissionsFor, renderWithProviders } from '../../test/fixtures';
import { BulkApproveForm, BulkBar } from './BulkBar';

const props = (over = {}) => ({
  module: 'upi',
  permissions: permissionsFor(['Maker']),
  username: 'maker1',
  meta: META,
  onDone: vi.fn(),
  onClear: vi.fn(),
  ...over,
});

describe('BulkBar eligibility messages (design 4.7)', () => {
  it('shows the R13 Pending Investigation refusal and disables bulk submit', () => {
    const r13 = makeCase({
      rule_code: 'R13',
      status_category: 'PENDING_INVESTIGATION',
      status: 'IN_PROGRESS',
      assigned_to: 'maker1',
      priority: 'MEDIUM',
      default_action_type: '',
    });
    renderWithProviders(<BulkBar {...props()} selected={[r13]} />);
    const reasons = screen.getByTestId('bulk-submit-reasons');
    expect(
      within(reasons).getByText(/Pending Investigation cases \(R13\) are never handled in bulk/),
    ).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Submit selected...' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('enables bulk submit for LOW R01 cases held by me, with no refusal shown', () => {
    const cases = [1, 2, 3].map(() => makeCase({ status: 'IN_PROGRESS', assigned_to: 'maker1' }));
    renderWithProviders(<BulkBar {...props()} selected={cases} />);
    expect(screen.getByText('3 cases selected')).toBeTruthy();
    expect(screen.queryByTestId('bulk-submit-reasons')).toBeNull();
    expect((screen.getByRole('button', { name: 'Submit selected...' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('lets the maker open bulk submit to choose an action when pre-fills differ', () => {
    const cases = [
      makeCase({ status: 'IN_PROGRESS', assigned_to: 'maker1', default_action_type: 'DRC_102' }),
      makeCase({ status: 'IN_PROGRESS', assigned_to: 'maker1', default_action_type: 'RET' }),
    ];
    renderWithProviders(<BulkBar {...props()} selected={cases} />);
    expect(screen.getByText(/do not share one pre-filled action/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Submit selected...' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('shows checker buttons only to a Checker', () => {
    const c = makeCase({ status: 'PENDING_APPROVAL' });
    renderWithProviders(<BulkBar {...props({ permissions: permissionsFor(['Checker']) })} selected={[c]} />);
    expect(screen.queryByRole('button', { name: 'Take selected' })).toBeNull();
    expect((screen.getByRole('button', { name: 'Approve selected...' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('tells read-only roles that bulk actions are not theirs', () => {
    renderWithProviders(
      <BulkBar {...props({ permissions: makeMe(['Auditor']).permissions })} selected={[makeCase()]} />,
    );
    expect(screen.getByText('Bulk actions are for Makers and Checkers.')).toBeTruthy();
  });

  it('renders nothing with no selection', () => {
    const { container } = renderWithProviders(<BulkBar {...props()} selected={[]} />);
    expect(container.querySelector('.bulk-bar')).toBeNull();
  });
});

describe('BulkApproveForm self-approval', () => {
  it('requires confirmation when the checker submitted any selected action', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const own = makeCase({ status: 'PENDING_APPROVAL', self_approval: true, rrn: '111' });
    const other = makeCase({ status: 'PENDING_APPROVAL' });
    renderWithProviders(<BulkApproveForm selected={[own, other]} onSubmit={onSubmit} onCancel={() => {}} />);
    expect(screen.getByText(/You submitted the action of 1 selected case \(111\)/)).toBeTruthy();
    const button = screen.getByRole('button', { name: 'Approve 2 (incl. self-approved)' });
    await userEvent.click(button);
    expect(await screen.findByText('Confirm the self-approval to continue')).toBeTruthy();
    expect(onSubmit).not.toHaveBeenCalled();
    await userEvent.click(screen.getByLabelText(/I confirm the approval of my own submissions/));
    await userEvent.click(button);
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({ remarks: undefined, confirm_self_approval: true }),
    );
  });
});
