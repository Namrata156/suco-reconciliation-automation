import { Button, Center, Paper, Stack, Text, Title } from '@mantine/core';
import { useQueryClient } from '@tanstack/react-query';

import { logout } from '../api/client';
import type { Me } from '../api/types';

/** Signed in, but holding none of the five roles: nothing to show. */
export function NoRolePage({ me }: { me: Me }) {
  const qc = useQueryClient();
  return (
    <Center mih="100vh" component="main">
      <Paper withBorder p="xl" w={460}>
        <Stack>
          <Title order={1} size="h3">
            No role assigned
          </Title>
          <Text>
            You are signed in as {me.username}, but your account holds none of the roles Administrator,
            Operations User, Maker, Checker or Auditor. Ask an Administrator to assign your role.
          </Text>
          <Button
            variant="default"
            onClick={async () => {
              try {
                await logout();
              } finally {
                qc.clear();
                qc.setQueryData(['me'], null);
              }
            }}
          >
            Sign out
          </Button>
        </Stack>
      </Paper>
    </Center>
  );
}
