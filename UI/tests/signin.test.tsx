import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

afterEach(() => vi.unstubAllGlobals());

describe('sign-in', () => {
  it('asks for sign-in when the service has no session, then opens the app as that person', async () => {
    const calls: string[] = [];
    const user = { id: 'u1', email: 'priya@harsha.com', name: 'Priya Shah', role: 'user', must_change: false };
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}`);
      if (url === '/api/auth/me') return new Response(JSON.stringify({ user: null, sign_in: true, needs_setup: false }));
      if (url === '/api/auth/login') return new Response(JSON.stringify({ user }));
      return new Response('{}', { status: 404 });
    });
    vi.resetModules();
    const { default: App } = await import('../src/App');
    location.hash = '#/home';
    render(<App />);
    await userEvent.type(await screen.findByLabelText(/E-mail/), 'priya@harsha.com');
    await userEvent.type(screen.getByLabelText(/^Password/), 'a long password here');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('heading', { name: 'Waiting for you' })).toBeInTheDocument();
    expect(calls).toContain('POST /api/auth/login');
    expect(screen.queryByRole('link', { name: /Profiles/ })).toBeNull();          // a user has no settings rail
  });
});
