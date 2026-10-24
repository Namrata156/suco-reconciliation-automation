import { describe, expect, it } from 'vitest';

import type { RoleName } from '../api/types';
import { makeMe } from '../test/fixtures';
import { menuFor, menuHref, MENU } from './roles';

const keys = (roles: RoleName[], builtSlice?: number) =>
  menuFor(makeMe(roles), builtSlice ? { builtSlice } : undefined).map((i) => i.key);

describe('role menus (design 4.2 / 3.5, from /me permissions)', () => {
  it('Maker: home, cases, my cases, files, business dates, settings, pending approvals, alerts', () => {
    expect(keys(['Maker'])).toEqual([
      'home',
      'cases',
      'my-cases',
      'files',
      'dates',
      'results-all',
      'settlement-all',
      'reports',
      'settings',
      'pending-approvals',
      'alerts',
    ]);
  });

  it('Checker: home, cases, awaiting my approval, files, business dates, settings, pending approvals', () => {
    expect(keys(['Checker'])).toEqual([
      'home',
      'cases',
      'awaiting-approval',
      'files',
      'dates',
      'results-all',
      'settlement-all',
      'reports',
      'settings',
      'pending-approvals',
      'alerts',
    ]);
  });

  it('Administrator: the Django admin link, no maker/checker items, no upload (P10b)', () => {
    expect(keys(['Administrator'])).toEqual([
      'home',
      'cases',
      'files',
      'dates',
      'results-all',
      'settlement-all',
      'reports',
      'settings',
      'pending-approvals',
      'alerts',
      'audit',
      'users',
      'django-admin',
    ]);
  });

  it('Administrator without staff status (admin_url null): no admin link', () => {
    const items = menuFor(makeMe(['Administrator'], { admin_url: null })).map((i) => i.key);
    expect(items).not.toContain('django-admin');
  });

  it('Operations User: upload, files and business dates (Slice 2), settings read-only (Slice 3)', () => {
    expect(keys(['Operations User'])).toEqual([
      'home',
      'cases',
      'upload',
      'files',
      'dates',
      'results-all',
      'settlement-all',
      'reports',
      'settings',
      'pending-approvals',
      'alerts',
    ]);
  });

  it('Auditor: read-only screens, alerts, the audit trail and the users list; no upload, no admin link', () => {
    expect(keys(['Auditor'])).toEqual([
      'home',
      'cases',
      'files',
      'dates',
      'results-all',
      'settlement-all',
      'reports',
      'settings',
      'pending-approvals',
      'alerts',
      'audit',
      'users',
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

  it('Slice 3 build: no alerts, audit or users screens', () => {
    expect(keys(['Administrator'], 3)).toEqual([
      'home',
      'cases',
      'files',
      'dates',
      'settings',
      'pending-approvals',
      'django-admin',
    ]);
  });

  it('module-less items (alerts, audit, users) keep their own address in every module', () => {
    for (const key of ['alerts', 'audit', 'users']) {
      const item = MENU.find((i) => i.key === key)!;
      expect(item.scoped).toBe(false);
      expect(menuHref(item, 'upi')).toBe(`/${key}`);
    }
    expect(MENU.find((i) => i.key === 'audit')?.matchPrefix).toBe(true);
  });

  it('Slice 1 build: no file screens', () => {
    expect(keys(['Operations User'], 1)).toEqual(['home', 'cases']);
  });

  it('a user holding Maker and Checker sees both work lists', () => {
    expect(keys(['Maker', 'Checker'])).toEqual([
      'home',
      'cases',
      'my-cases',
      'awaiting-approval',
      'files',
      'dates',
      'results-all',
      'settlement-all',
      'reports',
      'settings',
      'pending-approvals',
      'alerts',
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
    expect(keys(['Checker'], 4)).toContain('pending-approvals');
  });

  it('Slice 2 build: no settings screens', () => {
    expect(keys(['Checker'], 2)).toEqual(['home', 'cases', 'awaiting-approval', 'files', 'dates']);
  });

  it('pending approvals: all five roles read it (owner 2026-10-20); no per-date results menu item', () => {
    for (const role of ['Administrator', 'Operations User', 'Maker', 'Checker', 'Auditor'] as RoleName[]) {
      expect(keys([role])).toContain('pending-approvals');
    }
    expect(MENU.map((i) => i.key)).not.toContain('results');
    expect(MENU.find((i) => i.key === 'settings')?.matchPrefix).toBe(true);
  });

  it('never shows deferred items (NPCI uploads)', () => {
    expect(keys(['Checker', 'Maker'], 99)).not.toContain('npci-uploads');
    expect(MENU.find((i) => i.key === 'npci-uploads')?.slice).toBe('deferred');
  });

  it('builds module-scoped addresses', () => {
    const cases = MENU.find((i) => i.key === 'my-cases')!;
    expect(menuHref(cases, 'upi')).toBe('/upi/cases?mine=assigned');
    expect(
      menuHref(
        MENU.find((i) => i.key === 'home')!,
        'UPI',
      ),
    ).toBe('/upi');
  });
});
