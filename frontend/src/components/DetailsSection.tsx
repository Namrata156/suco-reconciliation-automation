/**
 * A section that starts collapsed (UI refinements 2026-10-27, U1): history,
 * earlier runs and technical detail stay on the page but out of the way, so
 * nothing is removed. One pattern everywhere: a native <details> whose
 * <summary> holds the section heading. The browser gives keyboard and
 * screen-reader support (expanded / collapsed) without extra state, and the
 * heading keeps its place in the page outline.
 */
import { Stack, Title } from '@mantine/core';
import { useState, type ReactNode } from 'react';

export function DetailsSection({
  title,
  id,
  order = 2,
  defaultOpen = false,
  lazy = false,
  children,
}: {
  title: ReactNode;
  /** The heading id (unique on the page); the section is labelled by it. */
  id: string;
  /** Heading level: 2 for a page section, 3 inside one. */
  order?: 2 | 3;
  defaultOpen?: boolean;
  /** Render the content only once opened (it loads data of its own). */
  lazy?: boolean;
  children: ReactNode;
}) {
  const [opened, setOpened] = useState(defaultOpen);
  return (
    <section aria-labelledby={id}>
      <details
        className="details-section"
        open={defaultOpen}
        onToggle={(e) => {
          if (e.currentTarget.open) setOpened(true);
        }}
      >
        <summary>
          <Title order={order} size={order === 2 ? 'h4' : 'h5'} id={id}>
            {title}
          </Title>
        </summary>
        <Stack gap="xs" mt="xs">
          {!lazy || opened ? children : null}
        </Stack>
      </details>
    </section>
  );
}
