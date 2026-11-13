/**
 * One business date (design 4.11, Amendment 1 step 3), laid out as the
 * numbered steps of the day (UI refinements U2, 3.1): a checklist of the seven
 * steps on top, each with a tick or "waiting", then one section per step:
 *   1 Files arrived       the expected-files checklist and files outside it
 *   2 Run reconciliation  the run panel with the "Run" button (Operations
 *                         User) and the earlier runs (collapsed)
 *   3 Results             one plain line and the results summary (design
 *                         4.12); technical detail in a collapsed "Details"
 *   4 Settlement tally    the settlement status and a link to it
 *   5 Cases to work       the date's cases
 *   6 Rev credit file     the CBS credit file: generate, download, upload the
 *                         checked file (CreditVoucherPanel; plan B, owner
 *                         2026-11-12). Before the SSKUPI step: the upload
 *                         approves the DRC 103 / TCC 103 credits that file
 *                         then carries
 *   7 SSKUPI file         generate, the files with Download, and why there
 *                         is nothing new (NpciFilePanel; owner: the box is
 *                         called "SSKUPI file" everywhere); a hint while
 *                         a rev file waits for its checked file
 * The date's status (contract A0) is the server's sentence; a value the SPA
 * does not know yet is shown as sent. The history is collapsed at the end.
 * Refreshes itself while a run is active or files are being processed
 * (lib/polling.ts).
 */
import {
  Alert,
  Anchor,
  Card,
  Group,
  List,
  SimpleGrid,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, type ReactNode } from 'react';
import { Link, useLocation, useParams } from 'react-router';

import { ApiError, getDate, startRun } from '../api/client';
import type { BusinessDateDetail, BusinessDateOverview } from '../api/types';
import { useMe } from '../auth/auth';
import { AutoRefresh } from '../components/AutoRefresh';
import { RunStatusBadge, SettlementStatusBadge } from '../components/Badges';
import { DateStatusBadge, StepBadge, type StepState } from '../components/DayStatus';
import { DetailsSection } from '../components/DetailsSection';
import { Field } from '../components/Field';
import { Checklist } from '../features/dates/Checklist';
import { isRunActive, RunPanel } from '../features/dates/RunPanel';
import { OperationHistory } from '../features/files/OperationHistory';
import { sskupiStep } from '../features/home/TodaysJob';
import {
  CreditVoucherPanel,
  revStep,
  useCreditVoucherList,
  useCreditVoucherPreview,
} from '../features/npci/CreditVoucherPanel';
import { NpciFilePanel } from '../features/npci/NpciFilePanel';
import { ResultsSummaryPanel } from '../features/results/ResultsSummaryPanel';
import { useModuleCode } from '../hooks/module';
import { formatCount, formatDate, formatDateTime, formatRupees } from '../lib/format';
import { pollInterval, pollingState, useUserActive } from '../lib/polling';
import { can } from '../lib/roles';
import { dateIsBusy } from './DatesPage';

/**
 * The date's reconciliation in one plain line (UI refinements 2026-10-27,
 * 3.2): "Reconciled 26-Sep-2026 10:42 - 69,120 transactions, 21 exceptions".
 * The rule set and build times are in the collapsed "Details".
 */
export function reconciledLine(
  d: Pick<BusinessDateOverview, 'matrix' | 'rule_evaluation' | 'cases'>,
): string {
  const counts = `${formatCount(d.matrix.match_groups)} transactions, ${formatCount(d.cases.total)} exceptions`;
  const r = d.rule_evaluation;
  if (r?.finished_at) return `Reconciled ${formatDateTime(r.finished_at)} - ${counts}`;
  // A rule run without an end time: still running, or stopped (the run panel says which).
  if (r) return `Rules started ${formatDateTime(r.started_at)}, not finished`;
  if (d.matrix.built) {
    const at = d.matrix.built_at ? ` ${formatDateTime(d.matrix.built_at)}` : '';
    return `Transactions matched${at} - ${formatCount(d.matrix.match_groups)} transactions; rules not applied yet`;
  }
  return 'Not reconciled yet';
}

export interface DateStep {
  n: number;
  key: 'files' | 'run' | 'results' | 'settlement' | 'cases' | 'rev' | 'sskupi';
  title: string;
  state: StepState;
  badge?: string;
}

/**
 * The seven steps of a date with their tick / "waiting" (UI refinements 3.1).
 * `rev` is the rev credit file's tick (revStep), from its own endpoint;
 * omitted = still loading ("waiting").
 */
export function dateSteps(d: BusinessDateOverview, rev?: Pick<DateStep, 'state' | 'badge'>): DateStep[] {
  const f = d.files;
  const filesDone =
    f.expected > 0 &&
    f.missing === 0 &&
    f.processing === 0 &&
    f.failed === 0 &&
    f.awaiting_date_selection === 0;
  const reconciled = !!d.rule_evaluation?.finished_at;
  const runAgain = d.status === 'READY_TO_RUN' || d.status === 'WAITING_FOR_FILES';
  const runFailed = d.last_run?.status === 'FAILED';

  let run: Pick<DateStep, 'state' | 'badge'>;
  if (isRunActive(d.last_run) || d.status === 'RUNNING') run = { state: 'running' };
  else if (runFailed) run = { state: 'attention', badge: 'Run failed' };
  else if (reconciled && d.status === 'READY_TO_RUN') run = { state: 'attention', badge: 'Run again' };
  else if (reconciled && !runAgain) run = { state: 'done' };
  else if (d.status === 'READY_TO_RUN') run = { state: 'attention', badge: 'Ready to run' };
  else run = { state: reconciled ? 'done' : 'waiting' };

  const settlement = d.settlement?.status;
  const sskupi = d.sskupi ? sskupiStep(d.sskupi, reconciled) : null;
  return [
    {
      n: 1,
      key: 'files',
      title: 'Files arrived',
      state: filesDone ? 'done' : f.failed > 0 || f.awaiting_date_selection > 0 ? 'attention' : 'waiting',
      badge: filesDone ? undefined : `${formatCount(f.expected - f.missing)} of ${formatCount(f.expected)}`,
    },
    { n: 2, key: 'run', title: 'Run reconciliation', ...run },
    { n: 3, key: 'results', title: 'Results', state: reconciled ? 'done' : 'waiting' },
    {
      n: 4,
      key: 'settlement',
      title: 'Settlement tally',
      state: settlement === 'TALLIED' ? 'done' : settlement === 'UNEXPLAINED' ? 'attention' : 'waiting',
      badge: settlement === 'TALLIED' ? 'Tallied' : settlement === 'UNEXPLAINED' ? 'Not tallied' : undefined,
    },
    {
      n: 5,
      key: 'cases',
      title: 'Cases to work',
      state: !reconciled ? 'waiting' : d.cases.active > 0 ? 'attention' : 'done',
      badge: reconciled
        ? d.cases.active > 0
          ? `${formatCount(d.cases.active)} open`
          : 'None open'
        : undefined,
    },
    { n: 6, key: 'rev', title: 'Rev credit file', state: 'waiting', ...rev },
    {
      n: 7,
      key: 'sskupi',
      title: 'SSKUPI file',
      state: sskupi?.state ?? 'waiting',
      badge: sskupi?.badge,
    },
  ];
}

/** One step's section: "3. Results" with its badge, the anchor the home page links to. */
function Step({
  step,
  children,
  prominent = false,
}: {
  step: DateStep;
  children: ReactNode;
  prominent?: boolean;
}) {
  const id = `step-${step.key}`;
  return (
    <Card
      withBorder
      padding="md"
      component="section"
      id={id}
      aria-labelledby={`${id}-title`}
      style={prominent ? { borderWidth: 2, borderColor: 'var(--mantine-color-blue-6)' } : undefined}
    >
      <Group justify="space-between" wrap="wrap" mb="sm">
        <Title order={2} size="h4" id={`${id}-title`}>
          {step.n}. {step.title}
        </Title>
        <StepBadge state={step.state} text={step.badge} />
      </Group>
      <Stack gap="sm">{children}</Stack>
    </Card>
  );
}

export function DateDetailPage() {
  const me = useMe();
  const module = useModuleCode();
  const qc = useQueryClient();
  const businessDate = useParams().businessDate ?? '';
  const { hash } = useLocation();
  const active = useUserActive();
  const busy = (d?: BusinessDateDetail) => !!d && dateIsBusy(d);

  const query = useQuery({
    queryKey: ['date', module, businessDate],
    queryFn: () => getDate(module, businessDate),
    enabled: /^\d{4}-\d{2}-\d{2}$/.test(businessDate),
    refetchInterval: (q) => pollInterval(busy(q.state.data), active),
  });
  const d = query.data;
  const polling = pollingState(busy(d), active);
  // The rev file's tick and the SSKUPI hint; fetched once the date is known to exist.
  const revList = useCreditVoucherList(module, businessDate, !!d);
  const revPreview = useCreditVoucherPreview(module, businessDate, !!d);

  // The home page links to a step (#step-sskupi): scroll to it once the page is there.
  const loaded = !!d;
  useEffect(() => {
    if (!loaded || !hash) return;
    document.getElementById(hash.slice(1))?.scrollIntoView({ block: 'start' });
  }, [loaded, hash]);

  const run = async () => {
    try {
      const r = await startRun(module, businessDate);
      notifications.show({
        color: 'green',
        title: 'Run started',
        message: `Run #${r.id} for ${formatDate(businessDate)} is queued.`,
      });
    } catch (e) {
      if (e instanceof ApiError && e.isConflict) void query.refetch();
      throw e;
    } finally {
      void qc.invalidateQueries({ queryKey: ['date', module, businessDate] });
      void qc.invalidateQueries({ queryKey: ['dates', module] });
      void qc.invalidateQueries({ queryKey: ['summary', module] });
      void qc.invalidateQueries({ queryKey: ['cases', module] });
      void qc.invalidateQueries({ queryKey: ['results-summary', module, businessDate] });
      void qc.invalidateQueries({ queryKey: ['settlement', module, businessDate] });
      void qc.invalidateQueries({ queryKey: ['npci-preview', module, businessDate] });
    }
  };

  if (!/^\d{4}-\d{2}-\d{2}$/.test(businessDate)) return <Alert color="red">Invalid date address.</Alert>;
  if (query.isPending) return <Skeleton h={300} />;
  if (query.error || !d) {
    const notFound = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Alert
        color="red"
        role="alert"
        title={notFound ? 'Nothing for this date' : 'The date could not be loaded'}
      >
        {notFound ? `No files or runs for ${formatDate(businessDate)}.` : query.error?.message}
      </Alert>
    );
  }

  const casesLink = `/${module}/cases?business_date=${d.business_date}&status=all`;
  const steps = dateSteps(d, revStep(revList.data, revPreview.data, !!d.rule_evaluation?.finished_at));
  const pendingRev = revList.data?.pending_approval ? revList.data.latest : null;
  const step = (key: DateStep['key']) => steps.find((s) => s.key === key)!;

  return (
    <Stack>
      <Anchor component={Link} to={`/${module}/dates`} size="sm">
        Back to the business dates
      </Anchor>
      <Group gap="sm" wrap="wrap">
        <Title order={1} size="h3">
          Business date {formatDate(d.business_date)}
        </Title>
        <DateStatusBadge status={d.status} label={d.status_label} />
      </Group>
      {d.status_detail && <Text size="sm">{d.status_detail}</Text>}
      <AutoRefresh
        polling={polling}
        what="a run or file processing is in progress"
        onRefresh={() => void query.refetch()}
        busy={query.isFetching}
      />
      {d.archived && (
        <Alert color="gray" title="Archived">
          This date has been archived. Loading more files for it is not supported until the
          reprocessing-after-archival rule is decided.
        </Alert>
      )}

      {/* The day's checklist: every step with its tick, each a link to its section. */}
      <Card withBorder padding="sm" component="nav" aria-label="Steps of the day">
        <SimpleGrid
          cols={{ base: 2, sm: 4, lg: 7 }}
          spacing="xs"
          component="ol"
          style={{ listStyle: 'none', padding: 0, margin: 0 }}
        >
          {steps.map((s) => (
            <li key={s.key}>
              <Anchor href={`#step-${s.key}`} underline="hover" c="inherit" size="sm" fw={600}>
                {s.n}. {s.title}
              </Anchor>
              <div>
                <StepBadge state={s.state} text={s.badge} />
              </div>
            </li>
          ))}
        </SimpleGrid>
      </Card>

      <Step step={step('files')}>
        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm" maw={800}>
          <Field label="Expected files present">
            <Text fw={700}>
              {d.files.expected - d.files.missing} of {d.files.expected}
            </Text>
            <Text size="xs">
              {d.files.loaded} loaded, {d.files.missing} missing
            </Text>
          </Field>
          <Field label="Files received">
            {/* The files list has no menu item since U1 (9.4); it opens from here. */}
            <Anchor
              component={Link}
              to={`/${module}/files?business_date=${d.business_date}`}
              fw={700}
              aria-label={`${formatCount(d.files.files_received)} files received: open the files list`}
            >
              {formatCount(d.files.files_received)}
            </Anchor>
            <Text size="xs">
              {d.files.files_processing} processing, {d.files.files_awaiting_date_selection} awaiting date,{' '}
              {d.files.files_failed} failed
            </Text>
          </Field>
        </SimpleGrid>
        <Checklist module={module} slots={d.checklist} />
        {d.unexpected_files.length > 0 && (
          <Alert color="orange" title="Files outside the checklist">
            <List size="sm">
              {d.unexpected_files.map((f) => (
                <List.Item key={f.id}>
                  <Anchor component={Link} to={`/${module}/files/${f.id}`}>
                    {f.filename}
                  </Anchor>{' '}
                  ({f.cycle ?? 'no cycle'}, {f.processing_status})
                </List.Item>
              ))}
            </List>
          </Alert>
        )}
      </Step>

      <Step step={step('run')}>
        <RunPanel overview={d} canRun={can(me.permissions, 'files.upload')} onRun={run} bare />
        {d.runs.length > 1 && (
          <DetailsSection title={`Earlier runs (${d.runs.length})`} id="runs-title" order={3}>
            <Table aria-label="Runs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th scope="col">Run</Table.Th>
                  <Table.Th scope="col">Status</Table.Th>
                  <Table.Th scope="col">Started by</Table.Th>
                  <Table.Th scope="col">Requested</Table.Th>
                  <Table.Th scope="col">Finished</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {d.runs.map((r) => (
                  <Table.Tr key={r.id}>
                    <Table.Td>#{r.id}</Table.Td>
                    <Table.Td>
                      <RunStatusBadge value={r.status} />
                    </Table.Td>
                    <Table.Td>{r.requested_by || '-'}</Table.Td>
                    <Table.Td>{formatDateTime(r.requested_at)}</Table.Td>
                    <Table.Td>{formatDateTime(r.finished_at)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </DetailsSection>
        )}
      </Step>

      <Step step={step('results')}>
        <Text fw={700}>{reconciledLine(d)}</Text>
        <ResultsSummaryPanel
          module={module}
          businessDate={d.business_date}
          runId={d.rule_evaluation?.id ?? null}
          enabled={d.matrix.built || d.rule_evaluation !== null}
        />
        {(d.matrix.built || d.rule_evaluation) && (
          <DetailsSection title="Details" id="date-details-title" order={3}>
            <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="sm" maw={800}>
              <Field label="Transactions matched (presence matrix)">
                {d.matrix.built
                  ? `${formatCount(d.matrix.match_groups)} transactions, built ${formatDateTime(d.matrix.built_at)}`
                  : 'Not built'}
              </Field>
              <Field label="Rules applied">
                {d.rule_evaluation
                  ? `Rule set ${d.rule_evaluation.rule_set}: ${formatCount(d.rule_evaluation.group_count)} transactions, started ${formatDateTime(d.rule_evaluation.started_at)}, finished ${formatDateTime(d.rule_evaluation.finished_at)} by ${d.rule_evaluation.triggered_by || 'system'}`
                  : 'Not run'}
              </Field>
            </SimpleGrid>
          </DetailsSection>
        )}
      </Step>

      <Step step={step('settlement')}>
        <Group gap="sm" wrap="wrap">
          <SettlementStatusBadge value={d.settlement?.status} />
          {d.settlement && <Text size="sm">Unexplained {formatRupees(d.settlement.unexplained_amount)}</Text>}
          {d.settlement && (
            <Anchor component={Link} to={`/${module}/dates/${d.business_date}/settlement`} size="sm">
              Open the settlement
            </Anchor>
          )}
        </Group>
      </Step>

      <Step step={step('cases')}>
        <Group gap="sm" wrap="wrap">
          <Anchor
            component={Link}
            to={casesLink}
            fw={700}
            aria-label={`${d.cases.total} cases: open the list`}
          >
            {formatCount(d.cases.total)} case(s)
          </Anchor>
          <Text size="sm">{formatCount(d.cases.active)} still open</Text>
          {d.cases.active > 0 && (
            <Anchor component={Link} to={`/${module}/cases?business_date=${d.business_date}`} size="sm">
              Open the cases still to work
            </Anchor>
          )}
        </Group>
      </Step>

      <Step step={step('rev')}>
        <CreditVoucherPanel module={module} businessDate={d.business_date} />
      </Step>

      <Step step={step('sskupi')} prominent>
        {pendingRev && (
          <Alert color="orange" title="Rev credit file not uploaded yet" role="note">
            {pendingRev.file_name} is generated but its checked file has not been uploaded. Upload it first (
            <Anchor href="#step-rev">step {step('rev').n}</Anchor>): the credits it approves (DRC 103 / TCC
            103) go into the SSKUPI file only after that, and a run of the date.
          </Alert>
        )}
        <NpciFilePanel module={module} businessDate={d.business_date} bare />
      </Step>

      <DetailsSection title="History" id="date-history-title">
        <OperationHistory entries={d.history} label="Business date history" />
      </DetailsSection>
    </Stack>
  );
}
