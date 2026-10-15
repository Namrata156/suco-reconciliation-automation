import { describe, expect, it } from 'vitest';

import type { RoleName } from '../api/types';
import { makeMe } from '../test/fixtures';
import { menuFor, menuHref, MENU } from './roles';

const keys = (roles: RoleName[], builtSlice?: number) =>
  menuFor(makeMe(roles), builtSlice ? { builtSlice } : undefined).map((i) => i.key);

describe('role menus (design 4.2 / 3.5, from /me permissions)', () => {
  it('Maker: home, cases, my cases', () => {
    expect(keys(['Maker'])).toEqual(['home', 'cases', 'my-cases']);
  });

  it('Checker: home, cases, awaiting my approval', () => {
    expect(keys(['Checker'])).toEqual(['home', 'cases', 'awaiting-approval']);
  });

  it('Administrator: the Django admin link, no maker/checker items', () => {
    expect(keys(['Administrator'])).toEqual(['home', 'cases', 'django-admin']);
  });

  it('Administrator without staff status (admin_url null): no admin link', () => {
    const items = menuFor(makeMe(['Administrator'], { admin_url: null })).map((i) => i.key);
    expect(items).not.toContain('django-admin');
  });

  it('Operations User and Auditor: read-only items only in Slice 1', () => {
    expect(keys(['Operations User'])).toEqual(['home', 'cases']);
    expect(keys(['Auditor'])).toEqual(['home', 'cases']);
  });

  it('a user holding Maker and Checker sees both work lists', () => {
    expect(keys(['Maker', 'Checker'])).toEqual(['home', 'cases', 'my-cases', 'awaiting-approval']);
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
