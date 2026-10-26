import { defineConfig } from '@playwright/test';

// Runs in the installed Chrome (no browser download) against the dev server, which serves scripts/qa-browser.js.
export default defineConfig({
  testDir: 'e2e',
  timeout: 240_000,
  use: { channel: 'chrome', baseURL: 'http://localhost:5175', bypassCSP: true },
  webServer: { command: 'npx vite --port 5175 --strictPort', url: 'http://localhost:5175', reuseExistingServer: !process.env.CI },
});
