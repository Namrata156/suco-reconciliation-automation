/**
 * Branding and session settings.
 *
 * BRAND: logo files supplied by the owner on 2026-10-24 (design P8), kept in
 * src/assets/ so the build bundles them. The full name is taken from the
 * banner logo.
 */
import logoBanner from './assets/suco_banner.jpg';
import logoIcon from './assets/suco_icon.png';

export const BRAND = {
  /** Short name, shown in the app header. */
  bankName: 'SUCO Bank',
  /** Full name, from the logo; login banner alt text and the print header. */
  bankFullName: 'SUCO Souharda Sahakari Bank',
  appName: 'Payment Reconciliation',
  /** Square icon (white tile, red/blue "S", "SUCO BANK"); 128x128 PNG cut from the banner. */
  logoIcon,
  /** Red banner with the icon and the Kannada and English names; 1200x216. */
  logoBanner,
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
