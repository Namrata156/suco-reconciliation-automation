import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../api/client';
import type { OperationLogEntry } from '../../api/types';
import { renderWithProviders } from '../../test/fixtures';
import { DateSelection } from './DateSelection';
import { operationSummary } from './OperationHistory';

const selection = {
  detected_dates: ['2026-07-13', '2026-07-14', '2026-07-15'],
  batch_business_date: '2026-07-14',
  preselected: '2026-07-14',
};

describe('CBS date selection (design 4.10 / Amendment 1)', () => {
  it('lists the dates found, pre-selects the batch date and confirms it', async () => {
    const onSelect = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<DateSelection selection={selection} canSelect onSelect={onSelect} />);
    expect((screen.getByLabelText('14-Jul-2026') as HTMLInputElement).checked).toBe(true);
    expect(screen.getByLabelText('13-Jul-2026')).toBeTruthy();
    expect(screen.getByText(/Pre-selected: 14-Jul-2026/)).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Confirm business date' }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('2026-07-14'));
  });

  it('lets the user choose another found date', async () => {
    const onSelect = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<DateSelection selection={selection} canSelect onSelect={onSelect} />);
    await userEvent.click(screen.getByLabelText('15-Jul-2026'));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm business date' }));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith('2026-07-15'));
  });

  it('shows the server refusal', async () => {
    const onSelect = vi
      .fn()
      .mockRejectedValue(
        new ApiError(409, { detail: "A file for this type, cycle and date is already loaded: 'cbs.xls'." }, ''),
      );
    renderWithProviders(<DateSelection selection={selection} canSelect onSelect={onSelect} />);
    await userEvent.click(screen.getByRole('button', { name: 'Confirm business date' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/already loaded/);
  });

  it('warns when the batch date is not in the file and pre-selects nothing', () => {
    renderWithProviders(
      <DateSelection
        selection={{ ...selection, batch_business_date: '2026-07-20', preselected: null }}
        canSelect
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText(/uploaded for 20-Jul-2026, but that date is not among the dates found/)).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Confirm business date' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it('is read-only for roles that may not choose', () => {
    renderWithProviders(<DateSelection selection={selection} canSelect={false} onSelect={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Confirm business date' })).toBeNull();
    expect(screen.getByText('An Operations User confirms the date.')).toBeTruthy();
    expect((screen.getByLabelText('13-Jul-2026') as HTMLInputElement).disabled).toBe(true);
  });
});

describe('operations history summary', () => {
  const entry = (o: Partial<OperationLogEntry>): OperationLogEntry => ({
    id: 1,
    operation: 'FILE_UPLOADED',
    operation_label: 'File uploaded',
    username: 'ops1',
    created_at: '2026-07-15T04:30:00Z',
    source: 'api',
    business_date: '2026-07-14',
    uploaded_file_id: 5,
    run_id: null,
    details: {},
    ...o,
  });

  it('says who chose which CBS date from which', () => {
    expect(
      operationSummary(
        entry({
          operation: 'BUSINESS_DATE_SELECTED',
          details: { detected_dates: ['2026-07-13', '2026-07-14'] },
        }),
      ),
    ).toBe('Chose 14-Jul-2026 from 13-Jul-2026, 14-Jul-2026');
    expect(operationSummary(entry({ operation: 'RUN_STARTED', run_id: 9 }))).toBe('Run #9');
    expect(
      operationSummary(entry({ operation: 'FILE_REFUSED', details: { filename: 'a.csv', errors: ['Dup.'] } })),
    ).toBe('a.csv - Dup.');
  });
});
