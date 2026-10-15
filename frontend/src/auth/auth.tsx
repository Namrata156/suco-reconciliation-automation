/**
 * The signed-in user (GET /api/v1/me/) and the login redirect helpers.
 * Nothing about the user is stored in the browser; the session cookie is the
 * only state, held by the browser itself (design 7.1).
 */
import { Center, Loader } from '@mantine/core';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createContext, useContext, type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router';

import { getMe } from '../api/client';
import type { Me } from '../api/types';
import { NoRolePage } from '../pages/NoRolePage';

export const ME_KEY = ['me'] as const;

export function useMeQuery() {
  return useQuery({ queryKey: ME_KEY, queryFn: getMe, staleTime: Infinity, retry: false });
}

const MeContext = createContext<Me | null>(null);

/** The signed-in user. Only valid inside <RequireAuth>. */
export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error('useMe() used outside <RequireAuth>');
  return me;
}

/** Test helper / provider for a known user. */
export function MeProvider({ me, children }: { me: Me; children: ReactNode }) {
  return <MeContext.Provider value={me}>{children}</MeContext.Provider>;
}

/**
 * A safe in-app return address: only a path on this site, never another
 * origin ("//evil", "https://...") - no open redirect.
 */
export function safeNext(next: string | null | undefined): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return '/';
  if (next.startsWith('/login')) return '/';
  return next;
}

export function loginPath(next: string, expired = false): string {
  const p = new URLSearchParams();
  if (expired) p.set('expired', '1');
  if (next && next !== '/') p.set('next', next);
  const q = p.toString();
  return q ? `/login?${q}` : '/login';
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { data: me, isPending, isError, error } = useMeQuery();
  const location = useLocation();
  if (isPending) {
    return (
      <Center h="100vh">
        <Loader aria-label="Loading" />
      </Center>
    );
  }
  if (isError) throw error;
  if (!me) {
    return <Navigate to={loginPath(location.pathname + location.search)} replace />;
  }
  if (me.roles.length === 0) return <NoRolePage me={me} />;
  return <MeProvider me={me}>{children}</MeProvider>;
}

/** Forget everything cached about the previous user. */
export function useResetSession() {
  const qc = useQueryClient();
  return () => {
    qc.clear();
    qc.setQueryData(ME_KEY, null);
  };
}
