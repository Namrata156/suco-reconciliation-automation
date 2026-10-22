/**
 * Role-dependent menus (design 4.2, role matrix 3.5).
 *
 * Driven by /api/v1/me/ "permissions" (api/base.py CAPABILITIES: the role
 * matrix as data), so the SPA never re-encodes which role may do what. The
 * SERVER still decides every permission (design 3.5 / 7.1); the menu is a
 * convenience. The whole planned menu is listed here, with the slice that
 * builds each screen; items whose screen is not built yet are left out.
 */
import type { Capability, Me, Permissions, RoleName } from '../api/types';

export const ROLES = {
  ADMINISTRATOR: 'Administrator',
  OPERATIONS_USER: 'Operations User',
  MAKER: 'Maker',
  CHECKER: 'Checker',
  AUDITOR: 'Auditor',
} as const satisfies Record<string, RoleName>;

export const hasRole = (roles: readonly string[] | undefined, role: RoleName) => !!roles?.includes(role);

/** A capability check that treats a missing permissions object as "no". */
export const can = (perms: Partial<Permissions> | undefined, cap: Capability) => perms?.[cap] === true;

export interface MenuItem {
  key: string;
  label: string;
  /** Path under /<module>/ (module-scoped) or an absolute path. */
  to: string;
  /** Module-scoped items are prefixed with the current module. */
  scoped: boolean;
  /** A plain link outside the SPA (the Django admin). */
  external?: boolean;
  /** The /me permission that shows the item. */
  capability: Capability;
  /** First slice whose screen backs the item; later slices are hidden. */
  slice: 1 | 2 | 3 | 4 | 'deferred';
  /** Also highlighted on the addresses below it (settings tabs). */
  matchPrefix?: boolean;
}

export const MENU: readonly MenuItem[] = [
  { key: 'home', label: 'Home', to: '', scoped: true, capability: 'cases.view', slice: 1 },
  { key: 'cases', label: 'Exception cases', to: 'cases', scoped: true, capability: 'cases.view', slice: 1 },
  {
    key: 'my-cases',
    label: 'My cases',
    to: 'cases?mine=assigned',
    scoped: true,
    capability: 'cases.work',
    slice: 1,
  },
  {
    key: 'awaiting-approval',
    label: 'Awaiting my approval',
    to: 'cases?mine=approval',
    scoped: true,
    capability: 'cases.approve',
    slice: 1,
  },
  {
    key: 'upload',
    label: 'Upload files',
    to: 'files/upload',
    scoped: true,
    capability: 'files.upload',
    slice: 2,
  },
  { key: 'files', label: 'Files', to: 'files', scoped: true, capability: 'files.view', slice: 2 },
  { key: 'dates', label: 'Business dates', to: 'dates', scoped: true, capability: 'files.view', slice: 2 },
  // Results and settlement are per business date (design 4.12 / 4.14): they
  // open from the business-date list and overview, not from a menu item.
  {
    key: 'settings',
    label: 'Settings',
    to: 'settings',
    scoped: true,
    capability: 'settings.view',
    slice: 3,
    matchPrefix: true,
  },
  // All five roles read the pending list; only Checkers act (owner 2026-10-20).
  {
    key: 'pending-approvals',
    label: 'Pending approvals',
    to: 'approvals',
    scoped: true,
    capability: 'settings.view',
    slice: 3,
  },
  // Not module-scoped (design 3.1); the audit lists filter by module instead.
  { key: 'alerts', label: 'Alerts', to: '/alerts', scoped: false, capability: 'alerts.view', slice: 4 },
  {
    key: 'audit',
    label: 'Audit trail',
    to: '/audit',
    scoped: false,
    capability: 'audit.view',
    slice: 4,
    matchPrefix: true,
  },
  {
    key: 'npci-uploads',
    label: 'NPCI uploads',
    to: 'npci-files',
    scoped: true,
    capability: 'cases.approve',
    slice: 'deferred',
  },
  // Read-only "who holds which role" (design 4.17, contract C1: Administrator only).
  { key: 'users', label: 'Users and roles', to: '/users', scoped: false, capability: 'users.admin', slice: 4 },
  {
    key: 'django-admin',
    label: 'Edit users (Django admin)',
    to: '/admin/',
    scoped: false,
    external: true,
    capability: 'users.admin',
    slice: 1,
  },
];

/** The slice the running build implements. */
export const BUILT_SLICE = 4;

/** Menu items for a user, limited to screens already built. */
export function menuFor(
  me: Pick<Me, 'permissions' | 'admin_url'>,
  { builtSlice = BUILT_SLICE }: { builtSlice?: number } = {},
): MenuItem[] {
  return MENU.filter((item) => {
    if (item.slice === 'deferred' || item.slice > builtSlice) return false;
    if (!can(me.permissions, item.capability)) return false;
    // The admin link only when the server gives one (Administrator with staff status).
    if (item.key === 'django-admin' && !me.admin_url) return false;
    return true;
  }).map((item) => (item.key === 'django-admin' && me.admin_url ? { ...item, to: me.admin_url } : item));
}

/** Resolves a menu item to an href for the current module. */
export function menuHref(item: MenuItem, moduleSlug: string): string {
  if (!item.scoped) return item.to;
  const base = `/${moduleSlug.toLowerCase()}`;
  return item.to ? `${base}/${item.to}` : base;
}
