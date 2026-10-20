/**
 * Server-side paging control. `label` names the list, so two pagers on one
 * screen keep unique accessible names ("Entries page 2" vs "Versions page 2").
 */
import { Pagination } from '@mantine/core';

export function ListPager({
  total,
  value,
  onChange,
  label,
}: {
  /** Number of pages. */
  total: number;
  value: number;
  onChange: (page: number) => void;
  label?: string;
}) {
  if (total <= 1) return null;
  const prefix = label ? `${label} ` : '';
  return (
    <Pagination
      total={total}
      value={value}
      onChange={onChange}
      getControlProps={(control) => ({ 'aria-label': `${prefix}${control} page` })}
      getItemProps={(p) => ({ 'aria-label': label ? `${label} page ${p}` : `Page ${p}` })}
    />
  );
}
