/**
 * Home page, top (UI refinements U2, items 1.1 and 1.2; contract A1): the
 * day's job for the summary's business date as four linked steps (Files ->
 * Run -> Cases -> SSKUPI file), each with its state, and the SSKUPI file
 * tile with a button to the date's SSKUPI step. Every role gets the same
 * block; the server decides what the date status is.
 */
import { Anchor, Button, Card, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { Link } from 'react-router';

import type { DayBlock, SskupiFull } from '../../api/types';
import { DateStatusBadge, SskupiStateBadge, StepBadge, type StepState } from '../../components/DayStatus';
import { formatCount, formatDate, formatDateTime } from '../../lib/format';
import { spaLink } from '../../lib/links';

export interface JobStep {
  key: 'files' | 'run' | 'cases' | 'sskupi';
  title: string;
  state: StepState;
  /** The badge text when it is not the default word. */
  badge?: string;
  line: string;
  /** Anchor on the date page. */
  anchor: string;
}

/** The SSKUPI step's state (also used by the date page's step 6). */
export function sskupiStep(
  s: Pick<SskupiFull, 'state' | 'state_label' | 'file_count'> &
    Partial<Pick<SskupiFull, 'waiting_rows' | 'not_downloaded'>>,
  reconciled: boolean,
): { state: StepState; badge: string } {
  const waiting = s.waiting_rows ?? 0;
  const notDownloaded = s.not_downloaded ?? [];
  // A file never downloaded keeps the date open (FILE_PENDING, owner Q2), whatever the latest file says.
  if (notDownloaded.length > 0) {
    return { state: 'attention', badge: `Download ${notDownloaded.join(', ')}` };
  }
  if (s.state === 'DOWNLOADED') {
    return waiting > 0
      ? { state: 'attention', badge: 'New rows for a next file' }
      : { state: 'done', badge: 'Downloaded' };
  }
  if (s.state === 'GENERATED')
    return { state: 'attention', badge: s.state_label || 'Generated, not downloaded' };
  if (!reconciled) return { state: 'waiting', badge: 'Waiting' };
  return waiting > 0
    ? { state: 'attention', badge: 'To generate' }
    : { state: 'waiting', badge: s.state_label || 'Waiting' };
}

/** The four steps of the home strip, from the summary's day block. */
export function jobSteps(day: DayBlock): JobStep[] {
  const f = day.files;
  const filesDone =
    f.expected > 0 &&
    f.missing === 0 &&
    f.processing === 0 &&
    f.failed === 0 &&
    f.awaiting_date_selection === 0;
  const steps: JobStep[] = [
    {
      key: 'files',
      title: 'Files',
      state: filesDone ? 'done' : f.failed > 0 ? 'attention' : 'waiting',
      badge: filesDone ? 'All loaded' : f.failed > 0 ? `${formatCount(f.failed)} failed` : undefined,
      line: `${formatCount(f.present)} of ${formatCount(f.expected)} expected files${f.missing ? `, ${formatCount(f.missing)} missing` : ''}`,
      anchor: 'step-files',
    },
  ];
  const run = day.run;
  const again = day.status === 'READY_TO_RUN' && run.state === 'DONE';
  steps.push({
    key: 'run',
    title: 'Run',
    state:
      run.state === 'RUNNING'
        ? 'running'
        : run.state === 'FAILED' || again
          ? 'attention'
          : run.state === 'DONE'
            ? 'done'
            : day.status === 'READY_TO_RUN'
              ? 'attention'
              : 'waiting',
    badge:
      run.state === 'FAILED'
        ? 'Failed'
        : again
          ? 'Run again'
          : day.status === 'READY_TO_RUN' && run.state === 'NOT_RUN'
            ? 'Ready to run'
            : undefined,
    line: day.results
      ? `Reconciled ${formatDateTime(day.results.reconciled_at)}: ${formatCount(day.results.transactions)} transactions, ${formatCount(day.results.exceptions)} exceptions`
      : run.error || 'Not reconciled yet',
    anchor: 'step-run',
  });
  steps.push({
    key: 'cases',
    title: 'Cases',
    state: !day.results ? 'waiting' : day.cases.open > 0 ? 'attention' : 'done',
    badge:
      day.results && day.cases.open > 0
        ? `${formatCount(day.cases.open)} open`
        : day.results
          ? 'None open'
          : undefined,
    line: `${formatCount(day.cases.total)} case(s) in all`,
    anchor: 'step-cases',
  });
  if (day.sskupi) {
    const s = sskupiStep(day.sskupi, !!day.results);
    steps.push({
      key: 'sskupi',
      title: 'SSKUPI file',
      state: s.state,
      badge: s.badge,
      line: day.sskupi.latest ? day.sskupi.latest.file_name : 'No file generated yet',
      anchor: 'step-sskupi',
    });
  }
  return steps;
}

function SskupiTile({ s, href }: { s: SskupiFull; href: string }) {
  const latest = s.latest;
  return (
    <Card withBorder padding="sm" component="section" aria-labelledby="home-sskupi-title">
      <Group justify="space-between" wrap="wrap">
        <Title order={2} size="h4" id="home-sskupi-title">
          SSKUPI file
        </Title>
        <SskupiStateBadge state={s.state} label={s.state_label} />
      </Group>
      <Stack gap={4} mt="xs">
        {(s.not_downloaded ?? []).length > 0 && (
          <Text size="sm" fw={700} c="orange">
            Not downloaded yet: {(s.not_downloaded ?? []).join(', ')}. Download it and upload it to the NPCI
            portal.
          </Text>
        )}
        {latest ? (
          <>
            <Text size="sm" fw={600}>
              {latest.file_name}
            </Text>
            <Text size="sm">
              {formatCount(latest.row_count)} row(s), generated by {latest.generated_by || '-'} on{' '}
              {formatDateTime(latest.generated_at)}.
            </Text>
            <Text size="sm">
              {latest.last_download
                ? `Downloaded ${latest.download_count === 1 ? 'once' : `${formatCount(latest.download_count)} times`}; last by ${latest.last_download.by}, ${formatDateTime(latest.last_download.at)}.`
                : 'Not downloaded yet.'}
            </Text>
          </>
        ) : (
          <Text size="sm">No SSKUPI file generated for this date yet.</Text>
        )}
        {s.waiting_rows > 0 && (
          <Text size="sm">
            {formatCount(s.waiting_rows)} row(s) ready for {s.next_file_name}.
          </Text>
        )}
        {s.changed_since_included > 0 && (
          <Text size="sm" c="orange">
            {formatCount(s.changed_since_included)} case(s) changed since they were included in a file.
          </Text>
        )}
        {s.held_for_checker > 0 && (
          <Text size="sm">
            {formatCount(s.held_for_checker)} case(s) wait for a Checker before they can go in.
          </Text>
        )}
      </Stack>
      <Group mt="sm">
        <Button
          component={Link}
          to={href}
          variant={s.can_generate || (s.not_downloaded ?? []).length > 0 ? 'filled' : 'light'}
        >
          {s.can_generate
            ? `Generate ${s.next_file_name}`
            : (s.not_downloaded ?? []).length > 0
              ? `Download ${(s.not_downloaded ?? [])[0]}`
              : 'Go to the SSKUPI file'}
        </Button>
      </Group>
    </Card>
  );
}

export function TodaysJob({ module, day }: { module: string; day: DayBlock }) {
  const base = spaLink(day.link) ?? `/${module}/dates/${day.business_date}`;
  const steps = jobSteps(day);
  return (
    <Stack gap="sm">
      <Card withBorder padding="sm" component="section" aria-labelledby="todays-job-title">
        <Group justify="space-between" wrap="wrap" gap="xs">
          <Title order={2} size="h4" id="todays-job-title">
            Today&apos;s job: business date {formatDate(day.business_date)}
          </Title>
          <Group gap="xs">
            <DateStatusBadge status={day.status} label={day.status_label} />
            <Anchor component={Link} to={base} size="sm">
              Open the date
            </Anchor>
          </Group>
        </Group>
        {day.status_detail && (
          <Text size="sm" mt={4}>
            {day.status_detail}
          </Text>
        )}
        <SimpleGrid
          cols={{ base: 1, sm: 2, lg: steps.length }}
          spacing="sm"
          mt="sm"
          component="ol"
          style={{ listStyle: 'none', padding: 0, margin: 0 }}
        >
          {steps.map((st, i) => (
            <li key={st.key}>
              <Anchor
                component={Link}
                to={`${base}#${st.anchor}`}
                underline="never"
                c="inherit"
                aria-label={`Step ${i + 1}, ${st.title}: ${st.badge ?? st.state}. ${st.line}`}
                display="block"
              >
                <Card withBorder padding="xs" h="100%">
                  <Group justify="space-between" gap={4} wrap="nowrap">
                    <Text fw={700}>
                      {i + 1}. {st.title}
                    </Text>
                    <StepBadge state={st.state} text={st.badge} />
                  </Group>
                  <Text size="xs" c="dimmed" mt={4}>
                    {st.line}
                  </Text>
                </Card>
              </Anchor>
            </li>
          ))}
        </SimpleGrid>
      </Card>
      {day.sskupi && <SskupiTile s={day.sskupi} href={`${base}#step-sskupi`} />}
    </Stack>
  );
}
