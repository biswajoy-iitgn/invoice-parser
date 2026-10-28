import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import pkg from './package.json' with { type: 'json' };

// Content Security Policy for the built app. Dev is left without it: Vite's dev server injects an inline React refresh
// preamble. Styles allow inline because Griffel (Fluent) writes its rules into <style> elements at runtime. Fonts are
// bundled (src/main.tsx), so nothing loads from another origin.
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self'",
  "img-src 'self' data: blob:",
  "frame-src blob:", // uploaded PDFs preview in an iframe
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ');

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'csp',
      apply: 'build',
      transformIndexHtml: () => [{ tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: CSP }, injectTo: 'head-prepend' }],
    },
  ],
  // the extraction service: profiles, models and reading files (PORT=8001 .venv/bin/python scripts/run_app.py)
  server: { port: 5173, proxy: { '/api': process.env.VITE_API_PROXY || 'http://127.0.0.1:8001' } },
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  // Browsers supported: the last two years of Chrome, Edge, Firefox and Safari (Safari 15.4+ on Mac and iPhone).
  build: { target: ['chrome109', 'edge109', 'firefox115', 'safari15.4'], chunkSizeWarningLimit: 900 }, // scripts/budget.mjs is the size gate (gzip), not Vite's minified-size hint
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/setup.ts'],
  },
});
