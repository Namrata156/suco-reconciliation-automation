/**
 * The day's plain statuses (UI refinements U2, contract A0 / A1a): the date
 * status, the SSKUPI file state and the tick / "waiting" of a step of the
 * day. The server's label is always shown, so a status value added later
 * (e.g. FILE_PENDING, owner Q2) reads correctly before the SPA knows it; the
 * colour only repeats the text (design 7.4).
 */
import { Badge } from '@mantine/core';

const DATE_STATUS_COLOR: Record<string, string> = {
  RUNNING: 'blue',
  WAITING_FOR_FILES: 'gray',
  READY_TO_RUN: 'blue',
  CASES_OPEN: 'orange',
  FILE_PENDING: 'orange',
  DONE: 'green',
};

export function DateStatusBadge({
  status,
  label,
}: {
  status: string | undefined;
  label: string | undefined;
}) {
  if (!status) return <>-</>;
  return (
    <Badge color={DATE_STATUS_COLOR[status] ?? 'gray'} variant={status === 'DONE' ? 'filled' : 'light'}>
      {label || status}
    </Badge>
  );
}

const SSKUPI_COLOR: Record<string, string> = { NONE: 'gray', GENERATED: 'orange', DOWNLOADED: 'green' };

export function SskupiStateBadge({ state, label }: { state: string; label: string }) {
  return (
    <Badge color={SSKUPI_COLOR[state] ?? 'gray'} variant="light">
      {label || state}
    </Badge>
  );
}

/** One step of the day: done (tick), waiting, running, or something to look at. */
export type StepState = 'done' | 'waiting' | 'running' | 'attention';

const STEP: Record<StepState, { color: string; text: string }> = {
  done: { color: 'green', text: 'Done' },
  waiting: { color: 'gray', text: 'Waiting' },
  running: { color: 'blue', text: 'Running' },
  attention: { color: 'orange', text: 'To do' },
};

/** "✓ Done" / "Waiting" / ...; `text` replaces the default word (e.g. "12 open"). */
export function StepBadge({ state, text }: { state: StepState; text?: string }) {
  const s = STEP[state];
  return (
    <Badge color={s.color} variant={state === 'done' ? 'filled' : 'light'}>
      {state === 'done' ? '✓ ' : ''}
      {text ?? s.text}
    </Badge>
  );
}
