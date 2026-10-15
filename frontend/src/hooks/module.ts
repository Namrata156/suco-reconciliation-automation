import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router';

import { getMeta, getSummary } from '../api/client';
import type { Choice } from '../api/types';

/** The current payment module code from the URL (/upi/... -> "upi"). */
export function useModuleCode(): string {
  const { module } = useParams();
  return (module ?? '').toLowerCase();
}

export function useMeta(module: string) {
  return useQuery({
    queryKey: ['meta', module],
    queryFn: () => getMeta(module),
    staleTime: 10 * 60_000,
    enabled: !!module,
  });
}

export function useSummary(module: string) {
  return useQuery({ queryKey: ['summary', module], queryFn: () => getSummary(module), enabled: !!module });
}

/** The label of a choice, or the raw value when the list lacks it. */
export function labelOf(choices: readonly Choice[] | undefined, value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '-';
  return choices?.find((c) => c.value === value)?.label ?? value;
}
