/**
 * NTSL self-check per cycle (design 4.14; settlement.selfcheck): PASS /
 * FAIL / MISSING with the four differences and any unmapped descriptions.
 *
 * Fee GST check (settlement.fee_gst, owner 2026-11-06): WARNING ONLY. Each
 * cycle shows a GST indicator next to its status (it never changes the
 * PASS / FAIL badge) and can open its fee / GST rows. Hidden for now behind
 * SHOW_GST_ON_FEES (lib/featureFlags): when off, the table is exactly as it
 * was before the GST check.
 */
import { Alert, Badge, Button, List, Stack, Table, Text, VisuallyHidden } from '@mantine/core';
import { Fragment, useState } from 'react';

import type {
  CheckFigure,
  GstCheck,
  GstCheckNote,
  GstCheckRow,
  SelfCheck,
  SelfCheckRow,
} from '../../api/types';
import { CheckStatusBadge } from '../../components/Badges';
import { SHOW_GST_ON_FEES } from '../../lib/featureFlags';
import { displayValue, formatAmount, formatCount, formatRupees } from '../../lib/format';

/** "12 of 12 cycles PASS" (the Slice 3 click-through checks this line). */
export function selfCheckLine(c: SelfCheck): string {
  if (c.cycles === 0) return 'No NTSL self-check for this date yet';
  const rest = [c.fail ? `${c.fail} FAIL` : '', c.missing ? `${c.missing} MISSING` : ''].filter(Boolean);
  return `${c.pass} of ${c.cycles} cycles PASS${rest.length ? ` (${rest.join(', ')})` : ''}`;
}

const diff = (f: CheckFigure) => formatAmount(f.difference);

function unmappedText(lines: unknown): string[] {
  if (!Array.isArray(lines)) return lines ? [displayValue(lines)] : [];
  return lines.map((l) => (typeof l === 'string' ? l : displayValue(l)));
}

// ---------------------------------------------------------------------------
// Fee GST check (warning only)
// ---------------------------------------------------------------------------
const FEE_LABELS: Record<string, string> = {
  FEE: 'Interchange fee',
  SWITCHING_FEE: 'NPCI switching fee',
  PSP_FEE: 'Payer PSP fee',
  REBATE: 'Switching fee rebate',
  MANDATE_FEE: 'Non-financial mandate fee',
  NRP_FEE: 'NRP raise fee',
};

const NOTE_TEXT: Record<GstCheckNote, string> = {
  OUTSIDE_TOLERANCE: 'Difference is over the tolerance',
  GST_WITHOUT_FEE: 'GST charged with no fee',
  FEE_WITHOUT_GST: 'Fee with no GST',
};

const plural = (n: number) => `${n} cycle${n === 1 ? '' : 's'}`;

/** The cycle's GST rows; null when there is no check (not checked, or a MISSING cycle). */
function gstRows(r: SelfCheckRow): GstCheckRow[] | null {
  if (r.status === 'MISSING' || !r.gst_check || !('rows' in r.gst_check)) return null;
  return (r.gst_check as GstCheck).rows;
}

/** Not checked: the self-check was stored before the GST check existed. */
const gstNotChecked = (r: SelfCheckRow) => r.status !== 'MISSING' && r.gst_check === null;

/** "GST on fees: 2 cycles with a warning", or null when there is none. */
export function gstWarningLine(c: SelfCheck): string | null {
  return c.gst_warnings > 0 ? `GST on fees: ${plural(c.gst_warnings)} with a warning` : null;
}

/** 0.18 -> "18%". */
function ratePercent(rate: string | undefined): string {
  const n = Number(rate);
  return Number.isFinite(n) ? `${Math.round(n * 10000) / 100}%` : '-';
}

const isZero = (v: string) => Number(v) === 0;

function GstSummary({ check }: { check: SelfCheck }) {
  if (check.results.length === 0) return null;
  const warning = gstWarningLine(check);
  const notChecked = check.results.filter(gstNotChecked).length;
  const checked = check.results.some((r) => gstRows(r) !== null);
  return (
    <>
      {warning ? (
        <Alert color="yellow" title={warning}>
          Warning only: it does not change PASS / FAIL. Open &quot;Show GST rows&quot; on the cycle to see the
          difference.
        </Alert>
      ) : (
        checked && <Text size="sm">GST on fees: no warnings.</Text>
      )}
      {notChecked > 0 && (
        <Text size="sm" c="dimmed">
          GST on fees: not checked for {plural(notChecked)} (checked before the GST check existed).
        </Text>
      )}
    </>
  );
}

function GstIndicator({ r }: { r: SelfCheckRow }) {
  if (gstNotChecked(r)) {
    return (
      <Badge color="gray" variant="outline" title="Checked before the GST check existed">
        Not checked
      </Badge>
    );
  }
  if (gstRows(r) === null) {
    return (
      <Text size="sm" c="dimmed" span title="No NTSL lines for this cycle">
        -
      </Text>
    );
  }
  return r.gst_warning ? (
    <Badge color="yellow" variant="light" title="Warning only: does not change PASS / FAIL">
      GST warning
    </Badge>
  ) : (
    <Text size="sm" span>
      OK
    </Text>
  );
}

function GstRowsTable({ cycle, gst }: { cycle: string; gst: GstCheck }) {
  const tolerance = formatRupees(gst.tolerance);
  const rows = gst.rows.filter((g) => !(isZero(g.fee) && isZero(g.ntsl_gst)));
  return (
    <Stack gap={4}>
      <Table withTableBorder aria-label={`GST on fees, cycle ${cycle}`} maw={1000}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th scope="col">Fee type</Table.Th>
            <Table.Th scope="col">Side</Table.Th>
            <Table.Th scope="col" ta="right">
              Fee (without GST)
            </Table.Th>
            <Table.Th scope="col" ta="right">
              Expected GST ({ratePercent(gst.rate)})
            </Table.Th>
            <Table.Th scope="col" ta="right">
              NTSL GST
            </Table.Th>
            <Table.Th scope="col" ta="right">
              Difference
            </Table.Th>
            <Table.Th scope="col">Within {tolerance}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((g) => (
            <Table.Tr
              key={`${g.fee_category}-${g.side}`}
              bg={g.within ? undefined : 'var(--mantine-color-yellow-light)'}
            >
              <Table.Td>{FEE_LABELS[g.fee_category] ?? g.fee_category}</Table.Td>
              <Table.Td>{g.side === 'DEBIT' ? 'Debit' : 'Credit'}</Table.Td>
              <Table.Td ta="right">{formatAmount(g.fee)}</Table.Td>
              <Table.Td ta="right">{formatAmount(g.expected_gst)}</Table.Td>
              <Table.Td ta="right">{formatAmount(g.ntsl_gst)}</Table.Td>
              <Table.Td ta="right">{formatAmount(g.difference)}</Table.Td>
              <Table.Td fw={g.within ? undefined : 700}>
                {g.within ? 'Yes' : `No${g.note ? `: ${NOTE_TEXT[g.note] ?? g.note}` : ''}`}
              </Table.Td>
            </Table.Tr>
          ))}
          {rows.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={7}>No fees or GST in this cycle.</Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
      <Text size="xs" c="dimmed">
        Difference = expected GST minus NTSL GST. Rows with no fee and no GST are not shown.
      </Text>
    </Stack>
  );
}

export function SelfCheckTable({ check }: { check: SelfCheck }) {
  const showGst = SHOW_GST_ON_FEES;
  const unmapped = check.results.filter((r) => r.unmapped_count > 0);
  const [open, setOpen] = useState<ReadonlySet<number>>(new Set());
  const toggle = (id: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  return (
    <Stack gap="xs">
      {showGst && <GstSummary check={check} />}
      <Table.ScrollContainer minWidth={showGst ? 1050 : 900}>
        <Table withTableBorder aria-label="NTSL self-check per cycle">
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Cycle</Table.Th>
              <Table.Th scope="col">Status</Table.Th>
              {showGst && <Table.Th scope="col">GST on fees</Table.Th>}
              <Table.Th scope="col" ta="right">
                NTSL lines
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Unmapped
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Sub-total debit diff
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Sub-total credit diff
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Settlement net diff
              </Table.Th>
              <Table.Th scope="col" ta="right">
                Final net diff
              </Table.Th>
              <Table.Th scope="col">Mapping</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {check.results.map((r) => {
              const rows = showGst ? gstRows(r) : null;
              const expanded = open.has(r.id);
              return (
                <Fragment key={r.id}>
                  <Table.Tr>
                    <Table.Td>{r.cycle}</Table.Td>
                    <Table.Td>
                      <CheckStatusBadge
                        value={r.status}
                        title={r.failures ? displayValue(r.failures) : undefined}
                      />
                    </Table.Td>
                    {showGst && (
                      <Table.Td>
                        <GstIndicator r={r} />
                        {rows !== null && (
                          <Button
                            size="compact-xs"
                            variant="subtle"
                            ml={4}
                            aria-expanded={expanded}
                            onClick={() => toggle(r.id)}
                          >
                            {expanded ? 'Hide' : 'Show'} GST rows{' '}
                            <VisuallyHidden>for cycle {r.cycle}</VisuallyHidden>
                          </Button>
                        )}
                      </Table.Td>
                    )}
                    <Table.Td ta="right">{formatCount(r.line_count)}</Table.Td>
                    <Table.Td ta="right">{formatCount(r.unmapped_count)}</Table.Td>
                    <Table.Td ta="right">{diff(r.subtotal_debit)}</Table.Td>
                    <Table.Td ta="right">{diff(r.subtotal_credit)}</Table.Td>
                    <Table.Td ta="right">{diff(r.settlement_net)}</Table.Td>
                    <Table.Td ta="right">{diff(r.final_net)}</Table.Td>
                    <Table.Td>v{r.map_version}</Table.Td>
                  </Table.Tr>
                  {rows !== null && expanded && (
                    <Table.Tr>
                      <Table.Td colSpan={showGst ? 10 : 9}>
                        <GstRowsTable cycle={r.cycle} gst={r.gst_check as GstCheck} />
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Fragment>
              );
            })}
            {check.results.length === 0 && (
              <Table.Tr>
                <Table.Td colSpan={showGst ? 10 : 9}>No NTSL lines checked for this date.</Table.Td>
              </Table.Tr>
            )}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {unmapped.length > 0 && (
        <Alert color="orange" title="Unmapped NTSL descriptions">
          {unmapped.map((r) => (
            <div key={r.id}>
              <Text size="sm" fw={600}>
                Cycle {r.cycle}
              </Text>
              <List size="sm">
                {unmappedText(r.unmapped_lines).map((l, i) => (
                  <List.Item key={i}>{l}</List.Item>
                ))}
              </List>
            </div>
          ))}
        </Alert>
      )}
    </Stack>
  );
}
