import { describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { RUNS } from '../src/data';

describe('home', () => {
  it('opens with the sample workspace and with documents the service read', async () => {
    expect(await renderHome()).toBe(true);
    cleanup();
    // what a browser keeps after a live batch: a document with a result but no bundled sample behind it
    const live = { id: 'R-2000-0', name: 'Carrier invoice.pdf', pages: 1, kind: 'vector', profile: 'freight', route: null, status: 'review',
      score: 0.5, sample: null, live: true, flagged: ['Invoice No', 'Vendor Name'] };
    localStorage.setItem('dx-workspace-v2', JSON.stringify({ runs: [{ id: 'R-2000', profile: 'freight', source: '1 file', started: 'Just now', by: 'Me', status: 'review', docs: [live] }, ...RUNS] }));
    expect(await renderHome()).toBe(true);
    expect(screen.getAllByText('Carrier invoice.pdf').length).toBeGreaterThan(0);
    expect(screen.getAllByText('2 values to check').length).toBeGreaterThan(0);
  });
});

/** Home rendered from whatever is in storage now (the workspace is read when App is first imported). */
async function renderHome(): Promise<boolean> {
  vi.resetModules();
  const { default: App } = await import('../src/App');
  location.hash = '#/home';
  render(<App />);
  await screen.findByRole('heading', { name: 'Waiting for you' }).catch(() => null);
  const ok = !screen.queryByText('This page ran into a problem') && !!screen.queryByRole('heading', { name: 'Waiting for you' });
  return ok;
}
