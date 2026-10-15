import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// Django's runserver (design 2.3). The browser only ever sees the Vite
// address, so the session cookie and CSRF behave as in production.
const DJANGO = 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  // Built files are served from the site root (design 2.4): by the intranet
  // web server, or by Django/WhiteNoise as the fallback. /api/ and /admin/
  // stay with Django.
  base: '/',
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: DJANGO, changeOrigin: false },
      '/admin': { target: DJANGO, changeOrigin: false },
      // The Django admin's own CSS/JS, so /admin/ also renders through 5173.
      '/static': { target: DJANGO, changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    // 1366x768 desktops on an intranet; keep the default browser targets of
    // Vite (current Edge / Chrome, design 7.5).
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    // Keep memory low on the 7.6 GB laptop (design 8.6).
    pool: 'threads',
    maxWorkers: 2,
  },
});
