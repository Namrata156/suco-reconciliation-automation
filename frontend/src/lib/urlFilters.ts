/**
 * List filters kept in the address (as in the case queue and the files
 * list), so a link or a bookmark reproduces the same list. Changing a filter
 * goes back to page 1.
 */
import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router';

export type UrlFilters<K extends string> = Partial<Record<K, string>> & { page: number; page_size: number };

export function filtersFromParams<K extends string>(
  params: URLSearchParams,
  keys: readonly K[],
  pageSize: number,
): UrlFilters<K> {
  const f = {} as Partial<Record<K, string>>;
  for (const k of keys) {
    const v = params.get(k);
    if (v) f[k] = v;
  }
  const page = Number(params.get('page') || '1');
  return { ...f, page: Number.isFinite(page) && page > 0 ? Math.floor(page) : 1, page_size: pageSize };
}

/** `keys` must be a module-level constant (it is a memo dependency). */
export function useUrlFilters<K extends string>(keys: readonly K[], pageSize = 50) {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => filtersFromParams(params, keys, pageSize), [params, keys, pageSize]);
  const setFilter = useCallback(
    (key: K | 'page', value: string | null | undefined) => {
      setParams((prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        if (key !== 'page') next.delete('page');
        return next;
      });
    },
    [setParams],
  );
  const reset = useCallback(() => setParams(new URLSearchParams()), [setParams]);
  return { filters, setFilter, reset };
}

/** "Showing 51-100 of 70,969"-style bounds for a page. */
export function pageBounds(count: number, page: number, pageSize: number) {
  const pages = Math.max(1, Math.ceil(count / pageSize));
  const first = count === 0 ? 0 : (page - 1) * pageSize + 1;
  const last = Math.min(count, page * pageSize);
  return { pages, first, last };
}
