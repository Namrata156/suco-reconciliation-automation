/**
 * "Find an RRN" (UI refinements U3, 4.5; contract B1): what the app holds for
 * one RRN - its transactions (match groups), cases and the rows of loaded
 * files. It replaces the per-date results list (~70,000 rows). Exactly one
 * transaction opens straight away (contract B1); otherwise, or when nothing is
 * found, this page lists what there is. Exact match only. Read-only.
 */
import { Alert, Anchor, Checkbox, Skeleton, Stack, Table, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, Navigate, useParams } from 'react-router';

import { ApiError, lookupRrn } from '../api/client';
import type { RrnLookup } from '../api/types';
import { StatusBadge } from '../components/Badges';
import { cleanRrn } from '../components/RrnSearchBox';
import { useMeta, useModuleCode } from '../hooks/module';
import { formatCount, formatDate, formatRupees } from '../lib/format';
import { spaLink } from '../lib/links';

/** "Rs 140.50 DEBIT" for a leg, or "not present". */
function legText(l: { present: boolean; amount: string | null; direction: string | null }): string {
  if (!l.present) return 'not present';
  return `${formatRupees(l.amount)}${l.direction ? ` ${l.direction}` : ''}`;
}

function Found({ r, module }: { r: RrnLookup; module: string }) {
  const { data: meta } = useMeta(module);
  return (
    <Stack gap="md">
      <Stack gap="xs" component="section" aria-labelledby="rrn-tx-title">
        <Title order={2} size="h4" id="rrn-tx-title">
          Transactions ({formatCount(r.transactions.length)})
        </Title>
        {r.transactions.length === 0 ? (
          <Text size="sm">No transaction (match group) has this RRN.</Text>
        ) : (
          <Table.ScrollContainer minWidth={800}>
            <Table withTableBorder aria-label={`Transactions with RRN ${r.rrn}`}>
              <Table.Thead>
                <Table.Tr>
                  {['Business date', 'Rule', 'CBS', 'NPCI', ''].map((h, i) => (
                    <Table.Th key={i} scope="col">
                      {h}
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {r.transactions.map((t) => (
                  <Table.Tr key={t.id}>
                    <Table.Td>
                      {formatDate(t.business_date)}
                      {t.is_superseded && (
                        <Text size="xs" c="dimmed">
                          Replaced by a later run
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>
                      {t.rule_name || t.rule_code || 'not evaluated'}
                      {t.rule_name && t.rule_code && (
                        <Text size="xs" c="dimmed" span>
                          {' '}
                          {t.rule_code}
                        </Text>
                      )}
                    </Table.Td>
                    <Table.Td>{legText(t.cbs)}</Table.Td>
                    <Table.Td>
                      {legText(t.npci)}
                      {t.npci.response_code ? ` (code ${t.npci.response_code})` : ''}
                    </Table.Td>
                    <Table.Td>
                      {spaLink(t.link) && (
                        <Anchor
                          component={Link}
                          to={spaLink(t.link)!}
                          aria-label={`Open the transaction of ${formatDate(t.business_date)}`}
                        >
                          Open
                        </Anchor>
                      )}
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Stack>

      <Stack gap="xs" component="section" aria-labelledby="rrn-cases-title">
        <Title order={2} size="h4" id="rrn-cases-title">
          Cases ({formatCount(r.cases.length)})
        </Title>
        {r.cases.length === 0 ? (
          <Text size="sm">No case has this RRN.</Text>
        ) : (
          <Table.ScrollContainer minWidth={800}>
            <Table withTableBorder aria-label={`Cases with RRN ${r.rrn}`}>
              <Table.Thead>
                <Table.Tr>
                  {['Case', 'Business date', 'Status', 'Rule', 'NPCI reply'].map((h) => (
                    <Table.Th key={h} scope="col">
                      {h}
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {r.cases.map((c) => (
                  <Table.Tr key={c.id}>
                    <Table.Td>
                      {spaLink(c.link) ? (
                        <Anchor component={Link} to={spaLink(c.link)!}>
                          Case #{c.id}
                        </Anchor>
                      ) : (
                        `Case #${c.id}`
                      )}
                    </Table.Td>
                    <Table.Td>{formatDate(c.business_date)}</Table.Td>
                    <Table.Td>
                      <StatusBadge value={c.status} choices={meta?.case_statuses} />
                    </Table.Td>
                    <Table.Td>{c.rule_name || c.rule_code || '-'}</Table.Td>
                    <Table.Td>{c.reply_label ?? '-'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Stack>

      <Stack gap="xs" component="section" aria-labelledby="rrn-rows-title">
        <Title order={2} size="h4" id="rrn-rows-title">
          Rows in loaded files ({formatCount(r.file_rows.length)})
        </Title>
        {r.file_rows.length === 0 ? (
          <Text size="sm">No loaded file has a row with this RRN.</Text>
        ) : (
          <Table.ScrollContainer minWidth={800}>
            <Table withTableBorder aria-label={`File rows with RRN ${r.rrn}`}>
              <Table.Thead>
                <Table.Tr>
                  {['File', 'Kind of row', 'Business date', 'Amount', 'Direction'].map((h) => (
                    <Table.Th key={h} scope="col">
                      {h}
                    </Table.Th>
                  ))}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {r.file_rows.map((f, i) => (
                  <Table.Tr key={`${f.uploaded_file_id}-${i}`}>
                    <Table.Td className="wrap-anywhere">
                      {spaLink(f.link) ? (
                        <Anchor component={Link} to={spaLink(f.link)!}>
                          {f.filename}
                        </Anchor>
                      ) : (
                        f.filename
                      )}
                    </Table.Td>
                    <Table.Td>{f.register_type}</Table.Td>
                    <Table.Td>{formatDate(f.business_date)}</Table.Td>
                    <Table.Td>{formatRupees(f.amount)}</Table.Td>
                    <Table.Td>{f.direction || '-'}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Stack>
    </Stack>
  );
}

export function RrnLookupPage() {
  const module = useModuleCode();
  const rrn = cleanRrn(useParams().rrn ?? '');
  const [includeReplaced, setIncludeReplaced] = useState(false);
  const query = useQuery({
    queryKey: ['rrn', module, rrn, includeReplaced],
    queryFn: () => lookupRrn(module, rrn, includeReplaced),
    enabled: rrn !== '',
  });
  const r = query.data;

  // Exactly one transaction: open it (contract B1); its page links the case and the source rows.
  const only = r && !includeReplaced && r.transactions.length === 1 ? spaLink(r.transactions[0]!.link) : null;
  if (only) return <Navigate to={only} replace />;

  return (
    <Stack>
      <Title order={1} size="h3">
        RRN {rrn}
      </Title>
      {query.isPending && rrn !== '' && <Skeleton h={200} />}
      {query.error && (
        <Alert color="red" role="alert" title="The RRN could not be looked up">
          {query.error instanceof ApiError
            ? [query.error.detail, ...(query.error.fieldErrors.rrn ?? [])].join(' ')
            : query.error.message}
        </Alert>
      )}
      {r && !r.found && (
        <Alert color="gray" title="Nothing found">
          No transaction, case or loaded file row has RRN {r.rrn}. The search is exact: check the digits.
        </Alert>
      )}
      {r && (r.replaced_count > 0 || includeReplaced) && (
        <Checkbox
          label={`Also show transactions replaced by a later run${r.replaced_count ? ` (${formatCount(r.replaced_count)})` : ''}`}
          checked={includeReplaced}
          onChange={(e) => setIncludeReplaced(e.currentTarget.checked)}
        />
      )}
      {r && r.found && <Found r={r} module={module} />}
    </Stack>
  );
}
