/**
 * Links the server sends for the SPA (alert "link", audit rows' "link";
 * Slice 4 contract A / B). They are followed only when they are an address
 * inside this SPA: a path on this site ("/upi/cases/123"), never another
 * origin ("//host", "https://..."), and never the Django admin (older
 * alerts mailed admin URLs; contract A says to use "link" only).
 */
export function spaLink(link: string | null | undefined): string | null {
  if (!link || !link.startsWith('/')) return null;
  if (link.startsWith('//') || link.startsWith('/\\')) return null;
  if (/^\/(admin|api|static)(\/|$|\?)/.test(link)) return null;
  return link;
}
