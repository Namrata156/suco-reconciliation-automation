/**
 * The settlement in ops' words (UI refinements U3, 5.1 / 5.2; contract B3):
 * one plain sentence first ("Tallied. CBS Rs X = NTSL Rs X after Rs Y
 * explained by ..." / "Not tallied. Rs X unexplained (N entries). See
 * settlement case #N."), the SOP check row under it, then what is explained,
 * by type (DRC / TCC / RET / RRC / late adjustments / other: count and
 * amount), like ops' tally sheet. Notes the server sends are shown as they
 * come (owner Q3 may move the late adjustments there).
 */
import { Alert, Anchor, Card, List, Stack, Table, Text, Title } from '@mantine/core';
import { Link } from 'react-router';

import type { SettlementSummary } from '../../api/types';
import { SettlementStatusBadge } from '../../components/Badges';
import { displayValue, formatCount, formatRupees } from '../../lib/format';
import { spaLink } from '../../lib/links';

/** "DRC (debited, not reversed) and RET" from the types in the SOP check. */
function typeList(s: SettlementSummary): string {
  const labels = s.explained_by_type.filter((t) => t.in_sop_check).map((t) => t.label);
  if (labels.length <= 1) return labels[0] ?? '';
  return `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
}

/** The lead sentence (contract B3 "Sentence the SPA can write"). */
export function settlementSentence(s: SettlementSummary): string {
  if (s.tallied) {
    const types = typeList(s);
    const after = types ? ` after ${formatRupees(s.explained)} explained by ${types}` : '';
    return `Tallied. CBS ${formatRupees(s.cbs_net)} = NTSL ${formatRupees(s.ntsl_net)}${after}.`;
  }
  if (s.status === 'UNEXPLAINED') {
    const see = s.settlement_case ? ` See settlement case #${s.settlement_case.id}.` : '';
    return `Not tallied. ${formatRupees(s.unexplained)} unexplained (${formatCount(s.unexplained_entries)} entries).${see}`;
  }
  return `${s.status_label || s.status}.`;
}

/** One note as text: {label, count, amount} or any value. */
export function noteText(key: string, v: unknown): string {
  if (v && typeof v === 'object' && 'label' in v) {
    const n = v as { label: string; count?: number; amount?: string | null };
    const parts = [
      n.count !== undefined ? `${formatCount(n.count)} row(s)` : '',
      n.amount ? formatRupees(n.amount) : '',
    ]
      .filter(Boolean)
      .join(', ');
    return parts ? `${n.label}: ${parts}` : n.label;
  }
  return `${key.replace(/_/g, ' ')}: ${displayValue(v)}`;
}

export function SettlementSummaryView({ s }: { s: SettlementSummary }) {
  const caseLink = s.settlement_case ? spaLink(s.settlement_case.link) : null;
  const notes = Object.entries(s.notes ?? {});
  return (
    <Stack gap="sm">
      <Card withBorder padding="sm" role="group" aria-label="Settlement summary">
        <Stack gap="xs">
          <SettlementStatusBadge value={s.status} />
          <Text fw={700} size="lg">
            {settlementSentence(s)}
          </Text>
          {caseLink && s.settlement_case && (
            <Anchor component={Link} to={caseLink}>
              Open settlement case #{s.settlement_case.id}
            </Anchor>
          )}
          {s.reasons.length > 0 && (
            <List size="sm" aria-label="Settlement reasons">
              {s.reasons.map((r) => (
                <List.Item key={r}>{r}</List.Item>
              ))}
            </List>
          )}
          {s.cbs_net !== null && (
            <Table withTableBorder aria-label="SOP check" maw={760}>
              <Table.Thead>
                <Table.Tr>
                  {['CBS net', 'NTSL net', 'Difference', 'Explained', 'Unexplained'].map((h) => (
                    <Table.Th key={h} scope="col" ta="right">
                      {h}
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                <Table.Tr>
                  {[s.cbs_net, s.ntsl_net, s.difference, s.explained, s.unexplained].map((v, i) => (
                    <Table.Td key={i} ta="right">
                      {formatRupees(v)}
                    </Table.Td>
                  ))}
                </Table.Tr>
              </Table.Tbody>
            </Table>
          )}
        </Stack>
      </Card>

      {s.explained_by_type.length > 0 && (
        <Stack gap="xs" component="section" aria-labelledby="explained-title">
          <Title order={2} size="h4" id="explained-title">
            Explained, by type
          </Title>
          <Table withTableBorder aria-label="Explained by type" maw={900}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th scope="col">Type</Table.Th>
                <Table.Th scope="col" ta="right">
                  Entries
                </Table.Th>
                <Table.Th scope="col" ta="right">
                  Amount
                </Table.Th>
                <Table.Th scope="col">Rules</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {s.explained_by_type.map((t) => (
                <Table.Tr key={t.type}>
                  <Table.Td>
                    {t.label}
                    {!t.in_sop_check && (
                      <Text size="xs" c="dimmed">
                        Explains NTSL&apos;s net adjusted amount, not the SOP difference
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td ta="right">{formatCount(t.count)}</Table.Td>
                  <Table.Td ta="right">{formatRupees(t.amount)}</Table.Td>
                  <Table.Td>
                    <Text size="xs">
                      {t.rules
                        .map(
                          (r) =>
                            `${r.rule} / ${r.g_rule} ${r.g_label}: ${formatCount(r.count)}, ${formatRupees(r.amount)}`,
                        )
                        .join('; ') || '-'}
                    </Text>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Stack>
      )}

      {(notes.length > 0 || (s.cbs_rows_without_valid_rrn?.count ?? 0) > 0) && (
        <Alert color="gray" title="Notes">
          <List size="sm">
            {notes.map(([k, v]) => (
              <List.Item key={k}>{noteText(k, v)}</List.Item>
            ))}
            {s.cbs_rows_without_valid_rrn && s.cbs_rows_without_valid_rrn.count > 0 && (
              <List.Item>
                {formatCount(s.cbs_rows_without_valid_rrn.count)} CBS row(s) without a valid RRN (
                {formatRupees(s.cbs_rows_without_valid_rrn.amount)}) are outside RRN matching and the SOP
                check.
              </List.Item>
            )}
          </List>
        </Alert>
      )}
    </Stack>
  );
}
