import { defineConfig } from '@playwright/test';

// Every page in the three browser engines people use: Chrome/Edge (the installed Chrome), Safari (WebKit) and Firefox
// (`npx playwright install webkit firefox` once). Runs against the dev server, which serves scripts/qa-browser.js.
export default defineConfig({
  testDir: 'e2e',
  timeout: 240_000,
  // reduced motion: axe measures colour contrast, which a page still fading in would fail at random
  use: { baseURL: 'http://localhost:5175', bypassCSP: true, contextOptions: { reducedMotion: 'reduce' } },
  projects: [
    { name: 'chrome', use: { channel: 'chrome' } },
    { name: 'webkit', use: { browserName: 'webkit' } },
    { name: 'firefox', use: { browserName: 'firefox' } },
  ],
  webServer: { command: 'npx vite --port 5175 --strictPort', url: 'http://localhost:5175', reuseExistingServer: !process.env.CI },
});
