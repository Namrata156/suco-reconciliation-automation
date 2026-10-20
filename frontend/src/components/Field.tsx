import { Text } from '@mantine/core';
import type { ReactNode } from 'react';

/** A small label over a value, for detail headers. */
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <div>{children}</div>
    </div>
  );
}
