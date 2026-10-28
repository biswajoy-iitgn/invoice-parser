// Sign-in, the forced password change after a temporary one, and creating the first administrator. Loaded only when needed.
import { useState } from 'react';
import { Body1, Button, Field, Input, MessageBar, MessageBarBody, Spinner } from '@fluentui/react-components';
import { Title } from './ui';
import { account } from './api';
import type { Account } from './api';

export type Gate = 'out' | 'setup' | 'change';

export default function SignIn({ gate, onDone }: { gate: Gate; onDone: (user: Account) => void }) {
  const [f, setF] = useState({ email: '', name: '', password: '', next: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const set = (k: keyof typeof f) => (_: unknown, d: { value: string }) => setF({ ...f, [k]: d.value });
  const submit = async () => {
    setBusy(true); setError('');
    try {
      const r = gate === 'setup' ? await account.setup(f.email, f.name, f.password)
        : gate === 'change' ? await account.password(f.password, f.next)
        : await account.login(f.email, f.password);
      setF({ email: '', name: '', password: '', next: '' });
      if (r.user.must_change) { setError(''); onDone(r.user); return; }
      onDone(r.user);
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  };
  const ok = gate === 'change' ? f.password && f.next.length >= 12 : f.email && f.password && (gate !== 'setup' || (f.name && f.password.length >= 12));
  const title = gate === 'setup' ? 'Create the administrator' : gate === 'change' ? 'Choose your password' : 'Sign in';
  return (
    <main className="signed-out">
      <form className="signed-out-card" onSubmit={(e) => { e.preventDefault(); if (ok && !busy) void submit(); }}>
        <span className="logo-chip signed-out-logo"><img src="/harsha-logo.png" alt="Harsha Engineers" className="logo" /></span>
        <Title>{title}</Title>
        {gate === 'setup' && <Body1 className="muted">No one can sign in yet. The first person becomes the administrator and adds everyone else.</Body1>}
        {gate === 'change' && <Body1 className="muted">Your password was set by an administrator. Choose your own to continue.</Body1>}
        {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
        {gate !== 'change' && <Field label="E-mail" required><Input type="email" autoComplete="username" value={f.email} onChange={set('email')} /></Field>}
        {gate === 'setup' && <Field label="Name" required><Input autoComplete="name" value={f.name} onChange={set('name')} /></Field>}
        <Field label={gate === 'change' ? 'Current password' : 'Password'} required hint={gate === 'setup' ? 'At least 12 characters' : undefined}>
          <Input type="password" autoComplete={gate === 'setup' ? 'new-password' : 'current-password'} value={f.password} onChange={set('password')} />
        </Field>
        {gate === 'change' && <Field label="New password" required hint="At least 12 characters"><Input type="password" autoComplete="new-password" value={f.next} onChange={set('next')} /></Field>}
        <Button appearance="primary" type="submit" disabled={!ok || busy} icon={busy ? <Spinner size="tiny" /> : undefined}>{gate === 'setup' ? 'Create' : gate === 'change' ? 'Save password' : 'Sign in'}</Button>
      </form>
    </main>
  );
}
