/**
 * Reports page (Phase 6 design 5.2): one card per report the user's role may
 * open (R2 matrix, lib/reports.ts), with a line on what it shows. Reports
 * whose screen comes in a later slice are listed as "not available yet", so
 * the user sees the whole set the spec names.
 */
import { Anchor, Badge, Card, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { Link } from 'react-router';

import { useMe } from '../auth/auth';
import { useModuleCode } from '../hooks/module';
import { reportsFor } from '../lib/reports';

export function ReportsPage() {
  const me = useMe();
  const module = useModuleCode();
  const reports = reportsFor(me, module);

  return (
    <Stack gap="sm">
      <Title order={1} size="h3">
        Reports
      </Title>
      <Text size="sm" c="dimmed">
        Each report filters on a business-date range and the module chosen in the header.
      </Text>
      {reports.length === 0 && <Text>Your role does not include any report.</Text>}
      <SimpleGrid
        cols={{ base: 1, md: 2 }}
        spacing="sm"
        component="ul"
        style={{ listStyle: 'none', padding: 0 }}
      >
        {reports.map((r) => (
          <Card key={r.key} withBorder component="li" aria-label={r.title}>
            <Group justify="space-between" align="flex-start" wrap="nowrap">
              {r.available ? (
                <Anchor component={Link} to={r.href} fw={600}>
                  {r.title}
                </Anchor>
              ) : (
                <Text fw={600}>{r.title}</Text>
              )}
              {!r.available && (
                <Badge color="gray" variant="light">
                  Not available yet
                </Badge>
              )}
            </Group>
            <Text size="sm" mt={4}>
              {r.description}
            </Text>
            {!r.available && (
              <Text size="xs" c="dimmed" mt={4}>
                Comes with Phase 6 slice {r.slice} (needs the server side).
              </Text>
            )}
          </Card>
        ))}
      </SimpleGrid>
    </Stack>
  );
}
