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
    rollupOptions: {
      output: {
        // Vendor code in a few stable chunks (Slice 4): they change only on a
        // dependency upgrade, so the browser keeps them cached across releases,
        // and no single chunk passes the 500 kB advisory. Screens are split
        // by lazy routes (AppRoutes.tsx).
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined;
          // CSS stays with its importer (main.tsx), so importing a library's
          // styles does not make the entry preload that library's JS.
          if (id.endsWith('.css')) return undefined;
          // Charts (Dashboard only, U5 8.3): @mantine/charts, recharts and the
          // packages only recharts uses, in their own chunk so that the lazy
          // Dashboard route alone loads them (the entry preloads "vendor" and
          // "vendor-mantine"). clsx stays out: Mantine core shares it. react-is is
          // here because only recharts uses it (check again if another package
          // starts to: the entry would then preload this chunk).
          const CHARTS =
            /[\\/]node_modules[\\/](@mantine[\\/]charts|recharts|@reduxjs|react-redux|redux|redux-thunk|immer|reselect|es-toolkit|victory-vendor|d3-[a-z-]+|internmap|decimal\.js-light|eventemitter3|tiny-invariant|use-sync-external-store|react-is)[\\/]/;
          if (CHARTS.test(id)) return 'vendor-charts';
          if (/[\\/]node_modules[\\/](@mantine[\\/]dates|dayjs)[\\/]/.test(id)) return 'vendor-dates';
          if (/[\\/]node_modules[\\/]@mantine[\\/]/.test(id)) return 'vendor-mantine';
          if (/[\\/]node_modules[\\/](zod|react-hook-form|@hookform)[\\/]/.test(id)) return 'vendor-forms';
          if (/[\\/]node_modules[\\/]@tanstack[\\/]/.test(id)) return 'vendor-tanstack';
          if (/[\\/]node_modules[\\/](react|react-dom|react-router|scheduler)[\\/]/.test(id)) return 'vendor-react';
          return 'vendor';
        },
      },
    },
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
