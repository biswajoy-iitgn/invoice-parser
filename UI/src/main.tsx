// Noto Sans served from the app (OFL-1.1): no request to a font CDN, so no visitor IP leaves the network.
import '@fontsource/noto-sans/400.css';
import '@fontsource/noto-sans/600.css';
import '@fontsource/noto-sans/700.css';
import '@fontsource/noto-sans-gujarati/400.css';
import '@fontsource/noto-sans-gujarati/600.css';
import '@fontsource/noto-sans-devanagari/400.css';
import '@fontsource/noto-sans-devanagari/600.css';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';

// A redeploy replaces the content-hashed page chunks, so an app opened before it can't load the pages it hasn't opened yet.
// Reload to pick up the new build: at most once a minute, so a chunk that is really missing can't loop.
window.addEventListener('vite:preloadError', (e) => {
  try {
    if (Date.now() - Number(sessionStorage.getItem('reloaded-for-build') || 0) < 60_000) return;
    sessionStorage.setItem('reloaded-for-build', String(Date.now()));
  } catch { return; }
  e.preventDefault();
  location.reload();
});

// FluentProvider lives in App, which follows the light or dark appearance.
ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
