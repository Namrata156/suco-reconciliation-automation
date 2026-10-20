import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '../../api/client';
import type { ChecklistSlot } from '../../api/types';
import { makeDateOverview, makeRun, renderWithProviders } from '../../test/fixtures';
import { Checklist, groupSlots } from './Checklist';
import { RunPanel } from './RunPanel';

describe('Run panel (design 4.11 / P6 / Amendment 1)', () => {
  it('confirms with the missing files listed, then starts the run', async () => {
    const onRun = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<RunPanel overview={makeDateOverview()} canRun onRun={onRun} />);
    expect(screen.getByText('No run for this date yet.')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Run reconciliation' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('1 expected file(s) missing')).toBeTruthy();
    expect(within(dialog).getByText(/Merchant Raw Data \(BBP\) ACQ 1C/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Start run' }));
    await waitFor(() => expect(onRun).toHaveBeenCalledTimes(1));
  });

  it('shows why a run cannot start and disables the button', () => {
    const overview = makeDateOverview({
      run_blockers: [{ code: 'FILES_PROCESSING', message: '3 file(s) of this date are still being processed.' }],
    });
    renderWithProviders(<RunPanel overview={overview} canRun onRun={vi.fn()} />);
    expect(screen.getByText('3 file(s) of this date are still being processed.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Run reconciliation' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the server refusal', async () => {
    const onRun = vi.fn().mockRejectedValue(new ApiError(409, { detail: 'Run #4 for this date is queued.' }, ''));
    renderWithProviders(<RunPanel overview={makeDateOverview()} canRun onRun={onRun} />);
    await userEvent.click(screen.getByRole('button', { name: 'Run reconciliation' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Start run' }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/Run #4 for this date is queued/);
  });

  it('shows the last run with its steps; re-run is the same button', () => {
    const run = makeRun({
      status: 'RUNNING',
      current_step: 'RULES',
      steps: { PRESENCE: { status: 'DONE' }, RULES: { status: 'RUNNING' }, SETTLEMENT: { status: 'PENDING' } },
      finished_at: null,
    });
    renderWithProviders(<RunPanel overview={makeDateOverview({ last_run: run })} canRun onRun={vi.fn()} />);
    const steps = screen.getByRole('table', { name: 'Steps of run #9' });
    expect(within(steps).getByRole('row', { name: /Presence matrix Done/ })).toBeTruthy();
    expect(within(steps).getByRole('row', { name: /Rule evaluation \+ case sync Running/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Run reconciliation again' })).toBeTruthy();
  });

  it('has no Run button for roles that may not run', () => {
    renderWithProviders(<RunPanel overview={makeDateOverview()} canRun={false} onRun={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /Run reconciliation/ })).toBeNull();
    expect(screen.getByText('An Operations User starts the run.')).toBeTruthy();
  });
});

describe('Expected-files checklist', () => {
  const slot = (o: Partial<ChecklistSlot>): ChecklistSlot => ({
    file_type_config_id: 3,
    file_type_code: 'RAW_DATA',
    label: 'Raw Data ACQ',
    cycle: '1C',
    state: 'MISSING',
    files: [],
    ...o,
  });
  const slots = [
    slot({ file_type_config_id: 1, file_type_code: 'CBS_GL_EXTRACT', label: 'CBS GL Extract', cycle: null,
      state: 'AWAITING_DATE_SELECTION', files: [{ id: 7, filename: 'cbs.xls', processing_status: 'AWAITING_DATE_SELECTION' }] }),
    slot({ state: 'LOADED', files: [{ id: 8, filename: 'raw1.csv', processing_status: 'LOADED' }] }),
    slot({ cycle: '2C' }),
  ];

  it('groups slots per file type', () => {
    expect(groupSlots(slots).map((r) => [r.label, r.slots.length])).toEqual([
      ['CBS GL Extract', 1],
      ['Raw Data ACQ', 2],
    ]);
  });

  it('shows each slot state in words and links present files', () => {
    renderWithProviders(<Checklist module="upi" slots={slots} />);
    expect(screen.getByRole('row', { name: /Raw Data ACQ 1 of 2/ })).toBeTruthy();
    const loaded = screen.getByRole('link', { name: 'Raw Data ACQ 1C: Loaded (raw1.csv)' });
    expect(loaded.getAttribute('href')).toBe('/upi/files/8');
    expect(screen.getByRole('link', { name: 'CBS GL Extract: Awaiting date (cbs.xls)' })).toBeTruthy();
    expect(screen.getByRole('img', { name: 'Raw Data ACQ 2C: Missing' }).textContent).toBe('2C Missing');
  });
});
