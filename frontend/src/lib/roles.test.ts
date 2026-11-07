import { describe, expect, it } from 'vitest';

import type { RoleName } from '../api/types';
import { makeMe } from '../test/fixtures';
import { menuFor, menuHref, MENU } from './roles';

const keys = (roles: RoleName[], builtSlice?: number) =>
  menuFor(makeMe(roles), builtSlice ? { builtSlice } : undefined).map((i) => i.key);

describe('role menus (design 4.2 / 3.5, from /me permissions; 11 items since U1 9.4)', () => {
  it('Maker: home, business dates, cases, pending approvals, reports, settings', () => {
    expect(keys(['Maker'])).toEqual([
      'home',
      'dashboard',
      'dates',
      'cases',
      'approvals',
      'reports',
      'settings',
    ]);
  });

  it('Checker: the same menu; approving happens in Approvals (U2 7.1)', () => {
    expect(keys(['Checker'])).toEqual([
      'home',
      'dashboard',
      'dates',
      'cases',
      'approvals',
      'reports',
      'settings',
    ]);
  });

  it('Administrator: the admin & audit items, no maker/checker items, no upload (P10b)', () => {
    expect(keys(['Administrator'])).toEqual([
      'home',
      'dashboard',
      'dates',
      'cases',
      'approvals',
      'reports',
      'settings',
      'audit',
      'users',
      'django-admin',
    ]);
  });

  it('Administrator without staff status (admin_url null): no admin link', () => {
    const items = menuFor(makeMe(['Administrator'], { admin_url: null })).map((i) => i.key);
    expect(items).not.toContain('django-admin');
  });

  it('Operations User: upload files', () => {
    expect(keys(['Operations User'])).toEqual([
      'home',
      'dashboard',
      'upload',
      'dates',
      'cases',
      'approvals',
      'reports',
      'settings',
    ]);
  });

  it('Auditor: read-only screens, the audit trail and the users list; no upload, no admin link', () => {
    expect(keys(['Auditor'])).toEqual([
      'home',
      'dashboard',
      'dates',
      'cases',
      'approvals',
      'reports',
      'settings',
      'audit',
      'users',
    ]);
  });

  it('removed from the menu in U1 (routes stay): my cases, files, all-dates results / settlement, alerts', () => {
    const all = MENU.map((i) => i.key);
    for (const key of ['my-cases', 'files', 'results-all', 'settlement-all', 'alerts']) {
      expect(all).not.toContain(key);
    }
  });

  it('audit trail, users and the Django admin link sit under "Admin & audit"; nothing else does', () => {
    expect(MENU.filter((i) => i.section === 'admin').map((i) => i.key)).toEqual([
      'audit',
      'users',
      'django-admin',
    ]);
  });

  it('users list on users.view (Administrator, Auditor); the admin link stays on users.admin', () => {
    expect(MENU.find((i) => i.key === 'users')?.capability).toBe('users.view');
    expect(MENU.find((i) => i.key === 'django-admin')?.capability).toBe('users.admin');
    // Even if the server sent an admin address, the Auditor has no users.admin.
    expect(menuFor(makeMe(['Auditor'], { admin_url: '/admin/' })).map((i) => i.key)).not.toContain(
      'django-admin',
    );
    for (const role of ['Operations User', 'Maker', 'Checker'] as RoleName[]) {
      expect(keys([role])).not.toContain('users');
    }
  });

  it('Slice 3 build: no audit or users screens', () => {
    expect(keys(['Administrator'], 3)).toEqual([
      'home',
      'dates',
      'cases',
      'approvals',
      'settings',
      'django-admin',
    ]);
  });

  it('module-less items (audit, users) keep their own address in every module', () => {
    for (const key of ['audit', 'users']) {
      const item = MENU.find((i) => i.key === key)!;
      expect(item.scoped).toBe(false);
      expect(menuHref(item, 'upi')).toBe(`/${key}`);
    }
    expect(MENU.find((i) => i.key === 'audit')?.matchPrefix).toBe(true);
  });

  it('Slice 1 build: no file screens', () => {
    expect(keys(['Operations User'], 1)).toEqual(['home', 'cases']);
  });

  it('a user holding Maker and Checker: one Approvals item', () => {
    expect(keys(['Maker', 'Checker'])).toEqual([
      'home',
      'dashboard',
      'dates',
      'cases',
      'approvals',
      'reports',
      'settings',
    ]);
  });

  it('no permissions: no menu', () => {
    const me = makeMe([]);
    expect(menuFor(me)).toEqual([]);
  });

  it('later slices follow the role matrix (upload: Ops only; audit: Auditor/Admin)', () => {
    expect(keys(['Operations User'], 4)).toContain('upload');
    expect(keys(['Administrator'], 4)).not.toContain('upload');
    expect(keys(['Auditor'], 4)).toContain('audit');
    expect(keys(['Maker'], 4)).not.toContain('audit');
    expect(keys(['Checker'], 4)).toContain('approvals');
  });

  it('Slice 2 build: no settings screens', () => {
    expect(keys(['Checker'], 2)).toEqual(['home', 'dates', 'cases']);
  });

  it('Approvals: all five roles read it (owner 2026-10-20); replaces both approval items (U2 7.1)', () => {
    for (const role of ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'] as RoleName[]) {
      expect(keys([role])).toContain('approvals');
    }
    expect(MENU.map((i) => i.key)).not.toContain('results');
    expect(MENU.map((i) => i.key)).not.toContain('awaiting-approval');
    expect(MENU.map((i) => i.key)).not.toContain('pending-approvals');
    expect(MENU.find((i) => i.key === 'approvals')?.label).toBe('Approvals');
    expect(MENU.find((i) => i.key === 'settings')?.matchPrefix).toBe(true);
  });

  it('never shows deferred items (NPCI uploads)', () => {
    expect(keys(['Checker', 'Maker'], 99)).not.toContain('npci-uploads');
    expect(MENU.find((i) => i.key === 'npci-uploads')?.slice).toBe('deferred');
  });

  it('builds module-scoped addresses', () => {
    const approvals = MENU.find((i) => i.key === 'approvals')!;
    expect(menuHref(approvals, 'upi')).toBe('/upi/approvals');
    expect(
      menuHref(
        MENU.find((i) => i.key === 'home')!,
        'UPI',
      ),
    ).toBe('/upi');
  });
});
