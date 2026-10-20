/**
 * Status / priority / flag badges. Design 7.4: never colour alone - every
 * badge carries its text, the colour only repeats it.
 */
import { Badge, Group } from '@mantine/core';

import type { CaseFlags, Choice, SlotState } from '../api/types';
import { labelOf } from '../hooks/module';

const PRIORITY_COLOR: Record<string, string> = { HIGH: 'red', MEDIUM: 'orange', LOW: 'blue', NONE: 'gray' };
const STATUS_COLOR: Record<string, string> = {
  OPEN: 'gray',
  IN_PROGRESS: 'blue',
  PENDING_APPROVAL: 'violet',
  APPROVED: 'teal',
  COMPLETED: 'green',
  CLOSED_SUPERSEDED: 'gray',
};
const DECISION_COLOR: Record<string, string> = {
  PENDING: 'violet',
  APPROVED: 'teal',
  REJECTED: 'red',
  WITHDRAWN: 'gray',
};

/** Flag labels as the queue shows them (design 4.4). */
export const FLAG_LABELS: Record<keyof CaseFlags, string> = {
  settlement: 'Settlement',
  overdue: 'Overdue',
  r07x: 'R07x',
  needs_review: 'Needs review',
  rrc_already_sent: 'RRC already sent',
  multiple_adjustment_rows: 'Multiple adjustment rows',
  no_deadline: 'No deadline',
  previous_day_r12: 'Previous-day R12',
};
const FLAG_COLOR: Partial<Record<keyof CaseFlags, string>> = {
  overdue: 'red',
  r07x: 'red',
  needs_review: 'orange',
  rrc_already_sent: 'orange',
  multiple_adjustment_rows: 'orange',
  settlement: 'grape',
};

export function PriorityBadge({ value, choices }: { value: string; choices?: Choice[] }) {
  return (
    <Badge color={PRIORITY_COLOR[value] ?? 'gray'} variant="filled" aria-label={`Priority ${value}`}>
      {labelOf(choices, value)}
    </Badge>
  );
}

export function StatusBadge({ value, choices }: { value: string; choices?: Choice[] }) {
  return (
    <Badge color={STATUS_COLOR[value] ?? 'gray'} variant="light">
      {labelOf(choices, value)}
    </Badge>
  );
}

export function DecisionBadge({ value }: { value: string }) {
  const label =
    { PENDING: 'Pending approval', APPROVED: 'Approved', REJECTED: 'Rejected', WITHDRAWN: 'Withdrawn' }[
      value
    ] ?? value;
  return (
    <Badge color={DECISION_COLOR[value] ?? 'gray'} variant="filled">
      {label}
    </Badge>
  );
}

export function SelfApprovedBadge() {
  return (
    <Badge color="yellow" variant="filled" title="Approved by the same user who submitted it">
      Self-approved
    </Badge>
  );
}

// --- files, checklist slots and runs (Slice 2) -------------------------------
type Variant = 'light' | 'filled' | 'outline';
const FILE_STATE: Record<SlotState, { label: string; color: string; variant: Variant }> = {
  LOADED: { label: 'Loaded', color: 'green', variant: 'light' },
  PROCESSING: { label: 'Processing', color: 'blue', variant: 'light' },
  AWAITING_DATE_SELECTION: { label: 'Awaiting date', color: 'violet', variant: 'filled' },
  FAILED: { label: 'Failed', color: 'red', variant: 'filled' },
  MISSING: { label: 'Missing', color: 'gray', variant: 'outline' },
};

export const fileStateLabel = (state: SlotState) => FILE_STATE[state]?.label ?? state;

/** A file's state, or a checklist slot's (MISSING); prefix e.g. the cycle ("1C Loaded"). */
export function FileStateBadge({ state, prefix, title }: { state: SlotState; prefix?: string; title?: string }) {
  const s = FILE_STATE[state] ?? { label: state, color: 'gray', variant: 'light' as Variant };
  return (
    <Badge color={s.color} variant={s.variant} title={title}>
      {prefix ? `${prefix} ${s.label}` : s.label}
    </Badge>
  );
}

const RUN_STATUS: Record<string, { label: string; color: string }> = {
  QUEUED: { label: 'Queued', color: 'gray' },
  RUNNING: { label: 'Running', color: 'blue' },
  SUCCEEDED: { label: 'Succeeded', color: 'green' },
  FAILED: { label: 'Failed', color: 'red' },
  PENDING: { label: 'Pending', color: 'gray' },
  DONE: { label: 'Done', color: 'green' },
};

/** A run's status, or one step's (PENDING / RUNNING / DONE / FAILED). */
export function RunStatusBadge({ value }: { value: string }) {
  const s = RUN_STATUS[value] ?? { label: value, color: 'gray' };
  return (
    <Badge color={s.color} variant={value === 'FAILED' ? 'filled' : 'light'}>
      {s.label}
    </Badge>
  );
}

const SETTLEMENT_STATUS: Record<string, { label: string; color: string }> = {
  TALLIED: { label: 'Tallied', color: 'green' },
  UNEXPLAINED: { label: 'Unexplained', color: 'red' },
  NOT_READY: { label: 'Not ready', color: 'gray' },
};

export function SettlementStatusBadge({ value }: { value: string | null | undefined }) {
  if (!value) return <>not computed</>;
  const s = SETTLEMENT_STATUS[value] ?? { label: value, color: 'gray' };
  return (
    <Badge color={s.color} variant={value === 'UNEXPLAINED' ? 'filled' : 'light'}>
      {s.label}
    </Badge>
  );
}

export function FlagBadges({ flags }: { flags: CaseFlags }) {
  const on = (Object.keys(FLAG_LABELS) as (keyof CaseFlags)[]).filter((k) => flags[k]);
  if (on.length === 0) return null;
  return (
    <Group gap={4} wrap="wrap">
      {on.map((k) => (
        <Badge key={k} size="sm" variant="outline" color={FLAG_COLOR[k] ?? 'gray'}>
          {FLAG_LABELS[k]}
        </Badge>
      ))}
    </Group>
  );
}
