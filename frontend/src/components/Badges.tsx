/**
 * Status / priority / flag badges. Design 7.4: never colour alone - every
 * badge carries its text, the colour only repeats it.
 */
import { Badge, Group } from '@mantine/core';

import type { CaseFlags, Choice } from '../api/types';
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
