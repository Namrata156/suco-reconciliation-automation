import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { BrowserRouter } from 'react-router';

import { ApiError } from './api/client';
import { AppRoutes } from './AppRoutes';
import { theme } from './theme';

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Recently viewed pages stay cached briefly so "back" is instant (7.6).
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        // No background refetch: requests renew the server session, so the
        // SPA only asks when the user does something.
        refetchOnWindowFocus: false,
        retry: (count, error) => !(error instanceof ApiError) && count < 1,
      },
      mutations: { retry: false },
    },
  });
}

/** Providers shared by the app and the component tests. */
export function Providers({ children, client }: { children: ReactNode; client?: QueryClient }) {
  const [qc] = useState(() => client ?? makeQueryClient());
  return (
    <MantineProvider theme={theme} defaultColorScheme="light">
      <Notifications position="top-right" limit={4} />
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    </MantineProvider>
  );
}

export function App() {
  return (
    <Providers>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </Providers>
  );
}
