import { Anchor, Stack, Text, Title } from '@mantine/core';
import { Link } from 'react-router';

export function NotFoundPage({ message }: { message?: string }) {
  return (
    <Stack p="md">
      <Title order={1} size="h3">
        Page not found
      </Title>
      <Text>{message ?? 'This address does not match any screen.'}</Text>
      <Anchor component={Link} to="/">
        Go to the home page
      </Anchor>
    </Stack>
  );
}
