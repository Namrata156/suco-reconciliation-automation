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

/**
 * Phase 6 capabilities the server does not send yet (docs/phase6_api_contract.txt
 * C1), with the existing ones that give the same roles (R2 matrix):
 *   reports.view          all five          = results.view
 *   reports.approvals     A, U, C           = audit.view (A, U) or cases.approve (C)
 *   reports.user_activity A, U              = audit.view
 * Used only while the key is ABSENT from /me; once sent, the server's value wins.
 */
export const CAPABILITY_FALLBACK: Partial<Record<Capability, readonly Capability[]>> = {
  'reports.view': ['results.view'],
  'reports.approvals': ['audit.view', 'cases.approve'],
  'reports.user_activity': ['audit.view'],
};

/** can(), plus the fallback above for a capability the server does not send yet. */
export function allows(perms: Partial<Permissions> | undefined, cap: Capability): boolean {
  const sent = perms?.[cap];
  if (typeof sent === 'boolean') return sent;
  return (CAPABILITY_FALLBACK[cap] ?? []).some((c) => can(perms, c));
}

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
  /**
   * First slice whose screen backs the item; later slices are hidden.
   * Phase 5 slices are 1-4, Phase 6 slices 6.1-6.4.
   */
  slice: 1 | 2 | 3 | 4 | 6.1 | 6.2 | 6.3 | 6.4 | 'deferred';
  /** Also highlighted on the addresses below it (settings tabs). */
  matchPrefix?: boolean;
  /** Also highlighted for these "mine" filter values (the queue's "Assigned to me"). */
  alsoMine?: readonly string[];
  /** Listed under the navbar's "Admin & audit" heading. */
  section?: 'admin';
}

export const MENU: readonly MenuItem[] = [
  // Menu cut from 16 items to 11 (UI refinements 2026-10-27, 9.4): "My cases"
  // is the queue's "Assigned to me" filter, the files list opens from the
  // business dates, the all-dates results and settlement are reports (5.5),
  // and alerts open from the header bell (9.3). Their routes stay, so old
  // links keep working.
  { key: 'home', label: 'Home', to: '', scoped: true, capability: 'cases.view', slice: 1 },
  // Tables now; the charts wait for the chart package (UI refinements U5 8.3).
  {
    key: 'dashboard',
    label: 'Dashboard',
    to: 'dashboard',
    scoped: true,
    capability: 'results.view',
    slice: 6.1,
  },
  {
    key: 'upload',
    label: 'Upload files',
    to: 'files/upload',
    scoped: true,
    capability: 'files.upload',
    slice: 2,
  },
  // Per-date results, settlement and the files list open from here.
  { key: 'dates', label: 'Business dates', to: 'dates', scoped: true, capability: 'files.view', slice: 2 },
  // Also highlighted while the queue shows "Assigned to me" (the old "My cases" item).
  {
    key: 'cases',
    label: 'Exception cases',
    to: 'cases',
    scoped: true,
    capability: 'cases.view',
    slice: 1,
    alsoMine: ['assigned'],
  },
  // One inbox, tabs Cases | Settings changes (UI refinements U2, 7.1); it replaces
  // "Awaiting my approval" and "Pending approvals". All five roles read it; only
  // Checkers act (owner 2026-10-20).
  {
    key: 'approvals',
    label: 'Approvals',
    to: 'approvals',
    scoped: true,
    capability: 'settings.view',
    slice: 3,
  },
  // The page shows each user only the reports their role may open (R2),
  // including the all-dates results and settlement (Phase 6 R3).
  {
    key: 'reports',
    label: 'Reports',
    to: 'reports',
    scoped: true,
    capability: 'reports.view',
    slice: 6.1,
    matchPrefix: true,
  },
  {
    key: 'settings',
    label: 'Settings',
    to: 'settings',
    scoped: true,
    capability: 'settings.view',
    slice: 3,
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
  // Not module-scoped (design 3.1); the audit lists filter by module instead.
  {
    key: 'audit',
    label: 'Audit trail',
    to: '/audit',
    scoped: false,
    capability: 'audit.view',
    slice: 4,
    matchPrefix: true,
    section: 'admin',
  },
  // Read-only "who holds which role" (design 4.17, contract C1; Auditor too, owner 2026-10-22).
  {
    key: 'users',
    label: 'Users and roles',
    to: '/users',
    scoped: false,
    capability: 'users.view',
    slice: 4,
    section: 'admin',
  },
  {
    key: 'django-admin',
    label: 'Edit users (Django admin)',
    to: '/admin/',
    scoped: false,
    external: true,
    capability: 'users.admin',
    slice: 1,
    section: 'admin',
  },
];

/** The slice the running build implements (Phase 6 slice 6.1). */
export const BUILT_SLICE = 6.1;

/** Menu items for a user, limited to screens already built. */
export function menuFor(
  me: Pick<Me, 'permissions' | 'admin_url'>,
  { builtSlice = BUILT_SLICE }: { builtSlice?: number } = {},
): MenuItem[] {
  return MENU.filter((item) => {
    if (item.slice === 'deferred' || item.slice > builtSlice) return false;
    if (!allows(me.permissions, item.capability)) return false;
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
