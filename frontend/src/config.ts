/**
 * Branding and session settings.
 *
 * BRAND: PLACEHOLDERS until the owner supplies the bank's name and logo file
 * (design P8: "please supply the logo file"). The logo is shown as text.
 */
export const BRAND = {
  bankName: 'SUCO Bank',
  appName: 'Payment Reconciliation',
  /** Text stand-in for the logo image (owner to supply the file, P8). */
  logoPlaceholder: '[Bank logo]',
};

/**
 * Session timeout (design 7.2, P7 approved: 15 minutes idle, renewed on each
 * request). The SPA warns at 13 minutes and returns to login at 15.
 */
export const SESSION = {
  idleMs: 15 * 60 * 1000,
  warnAtMs: 13 * 60 * 1000,
  checkEveryMs: 5 * 1000,
};
