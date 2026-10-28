import { useEffect, useState } from 'react';
import {
  Button, Dropdown, Option, Field, Input, Switch, Text, Caption1, SearchBox,
  Persona, Tooltip, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger, MessageBar, MessageBarBody, MessageBarTitle, Spinner,
} from '@fluentui/react-components';
import { PersonAddRegular, ShieldCheckmarkRegular, ArrowDownloadRegular, ArrowSyncRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, StatusBadge, EmptyState, RHead, RHeader, RBody, RRow, RCell, RTable, count } from '../ui';
import { usePaging } from '../sheet';
import { verify, stamp } from '../audit';
import { USERS } from '../data';
import { account, service } from '../api';
import type { Account, EnginePackage, ServerAuditEntry, UpdatesReport } from '../api';
import type { Role, User } from '../types';

const ROLES: Record<Role, string> = { user: 'User', admin: 'Sysadmin' };
const GROUPS = ['Finance approvers', 'Stores', 'Procurement'];

/** People who can sign in. With the service, accounts live there (auth.py): an administrator adds someone with a temporary
 * password, which they change at first sign-in. Without it, the sample list. */
export function Users() {
  const { notify, log, me } = useApp();
  const [users, setUsers] = useState<User[]>(USERS);
  const [live, setLive] = useState(false);
  const [inv, setInv] = useState<{ email: string; name: string; role: Role; password: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const fromAccount = (a: Account): User => ({ id: a.id, name: a.name, email: a.email, role: a.role, groups: [], last: a.disabled ? 'Switched off' : a.last_login ? new Date(a.last_login * 1000).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : 'Not signed in yet' });
  const load = () => account.users().then((r) => { setUsers(r.users.map(fromAccount)); setLive(true); }).catch(() => { /* no service: the sample list */ });
  useEffect(() => { void load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const valid = inv && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(inv.email) && (!live || (inv.name.trim() && inv.password.length >= 12));
  const setRole = async (u: User, role: Role) => {
    try {
      if (live) await account.update(u.id, { role });
      setUsers(users.map((x) => (x.id === u.id ? { ...x, role } : x)));
      log('Changed role', `${u.name}, ${ROLES[role]}`);
      notify(`${u.name} is now a ${ROLES[role].toLowerCase()}`);
    } catch (e) { notify("The role didn't change", 'error', (e as Error).message); }
  };
  const add = async () => {
    if (!inv || !valid) return;
    setBusy(true);
    try {
      if (live) {
        await account.add({ email: inv.email, name: inv.name.trim(), role: inv.role, password: inv.password });
        await load();
        notify(`${inv.name.trim()} added`, 'success', 'Give them the temporary password; they choose their own at first sign-in.');
      } else {
        setUsers([...users, { id: `u-${Date.now()}`, name: inv.name || inv.email.split('@')[0], email: inv.email, role: inv.role, groups: [], last: 'Invited' }]);
        notify('Invitation sent', 'success', inv.email);
      }
      log('Added a person', `${inv.email}, ${ROLES[inv.role]}`);
      setInv(null);
    } catch (e) { notify("The person wasn't added", 'error', (e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="page">
      <PageHeader title="People" description="Users upload and review documents. Sysadmins also manage profiles, engines, and databases.">
        <Button appearance="primary" icon={<PersonAddRegular />} onClick={() => setInv({ email: '', name: '', role: 'user', password: '' })}>Add a person</Button>
      </PageHeader>
      <div className="table-card">
        <RTable layout="auto" aria-label="People">
          <RHeader><RRow><RHead>Name</RHead><RHead>Role</RHead>{!live && <RHead>Approver groups</RHead>}<RHead>Last signed in</RHead></RRow></RHeader>
          <RBody>
            {users.map((u) => (
              <RRow key={u.id}>
                <RCell data-label="Name"><Persona name={u.name} secondaryText={u.email} avatar={{ color: 'brand' }} /></RCell>
                <RCell data-label="Role">
                  <Dropdown aria-label={`Role for ${u.name}`} value={ROLES[u.role]} selectedOptions={[u.role]} disabled={u.id === me.id}
                    onOptionSelect={(_, d) => void setRole(u, d.optionValue as Role)}>
                    {Object.entries(ROLES).map(([k, v]) => <Option key={k} value={k}>{v}</Option>)}
                  </Dropdown>
                  {u.id === me.id && <Caption1 block className="muted">{"You can't change your own role"}</Caption1>}
                </RCell>
                {!live && (
                  <RCell data-label="Approver groups">
                    <Dropdown aria-label={`Groups for ${u.name}`} multiselect placeholder="None" value={u.groups.join(', ')} selectedOptions={u.groups}
                      onOptionSelect={(_, d) => { setUsers(users.map((x) => (x.id === u.id ? { ...x, groups: d.selectedOptions } : x))); log('Changed approver groups', `${u.name}, ${d.selectedOptions.join(', ') || 'none'}`); }}>
                      {GROUPS.map((g) => <Option key={g}>{g}</Option>)}
                    </Dropdown>
                  </RCell>
                )}
                <RCell data-label="Last signed in">{u.last}</RCell>
              </RRow>
            ))}
          </RBody>
        </RTable>
      </div>

      <Dialog open={!!inv} onOpenChange={(_, d) => !d.open && setInv(null)}>
        <DialogSurface>
          {inv && (
            <form onSubmit={(e) => { e.preventDefault(); void add(); }}>
              <DialogBody>
                <DialogTitle>Add a person</DialogTitle>
                <DialogContent className="form">
                  <Field label="Email address" required validationState={inv.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(inv.email) ? 'error' : 'none'} validationMessage={inv.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(inv.email) ? 'Enter an email address like name@harsha.com' : undefined}>
                    <Input type="email" autoComplete="off" value={inv.email} onChange={(_, d) => setInv({ ...inv, email: d.value })} />
                  </Field>
                  <Field label="Name" required={live}><Input autoComplete="off" value={inv.name} onChange={(_, d) => setInv({ ...inv, name: d.value })} /></Field>
                  <Field label="Role">
                    <Dropdown value={ROLES[inv.role]} selectedOptions={[inv.role]} onOptionSelect={(_, d) => setInv({ ...inv, role: d.optionValue as Role })}>
                      {Object.entries(ROLES).map(([k, v]) => <Option key={k} value={k}>{v}</Option>)}
                    </Dropdown>
                  </Field>
                  {live && <Field label="Temporary password" required hint="At least 12 characters. They choose their own at first sign-in."><Input type="password" autoComplete="new-password" value={inv.password} onChange={(_, d) => setInv({ ...inv, password: d.value })} /></Field>}
                </DialogContent>
                <DialogActions>
                  <Button appearance="primary" type="submit" disabled={!valid || busy}>{live ? 'Add' : 'Send invitation'}</Button>
                  <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
                </DialogActions>
              </DialogBody>
            </form>
          )}
        </DialogSurface>
      </Dialog>
    </div>
  );
}

type LogRow = { key: string; at: string; actor: string; action: string; target: string; hash: string };
const short = (id: string) => (id.length > 12 ? id.slice(0, 8) : id);

/** The database's audit log when one is connected (hash-chained there, append-only); otherwise what this browser recorded. */
export function Audit() {
  const { notify, audit } = useApp();
  const [server, setServer] = useState<{ rows: ServerAuditEntry[]; more: boolean } | null>(null);
  const [offline, setOffline] = useState('');
  const [checked, setChecked] = useState<{ at: string; total: number; broken: string | null } | null>(null);
  const [checking, setChecking] = useState(false);
  const [q, setQ] = useState('');
  useEffect(() => {
    service.auditEntries().then((r) => setServer({ rows: r.entries, more: r.more }))
      .catch((e: Error) => setOffline(e.message === 'no database configured' ? 'No database is connected, so this log covers what was done in this browser only.' : `The audit log could not be loaded: ${e.message}`));
  }, []);
  const all: LogRow[] = server
    ? server.rows.map((e) => ({ key: String(e.seq), at: e.created_at.slice(0, 19).replace('T', ' '), actor: e.user_id ? short(e.user_id) : 'Service', action: e.action, target: `${e.entity} ${short(e.entity_id)}`, hash: e.row_hash }))
    : audit.map((a) => ({ key: a.hash || a.at + a.action, ...a }));
  const rows = all.filter((a) => !q || [a.at, a.actor, a.action, a.target].some((v) => v.toLowerCase().includes(q.toLowerCase())));
  const paged = usePaging(rows, q, 'entries');
  const more = async () => {
    if (!server) return;
    const r = await service.auditEntries(server.rows[server.rows.length - 1]?.seq);
    setServer({ rows: [...server.rows, ...r.entries], more: r.more });
  };
  const check = async () => {
    setChecking(true);
    try {
      if (server) {
        const r = await service.verifyChain();
        setChecked({ at: stamp(), total: r.entries, broken: r.intact ? null : `entry ${r.first_altered_entry}` });
      } else {
        const broken = await verify(audit);
        setChecked({ at: stamp(), total: audit.length, broken: broken < 0 ? null : audit[broken].at });
      }
    } catch (e) {
      notify("The log couldn't be checked", 'error', (e as Error).message);
    }
    setChecking(false);
  };
  useEffect(() => {
    if (checked) notify(checked.broken === null ? 'Log verified' : 'The log has been changed', checked.broken === null ? 'success' : 'error', checked.broken === null ? `${count(checked.total, 'entry', 'entries')}, chain intact` : `First changed: ${checked.broken}`);
  }, [checked]); // eslint-disable-line react-hooks/exhaustive-deps
  const exportCsv = () => {
    const text = [['Time', 'Who', 'Action', 'On', 'Hash'], ...all.map((a) => [a.at, a.actor, a.action, a.target, a.hash])].map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(',')).join('\n');
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: 'audit-log.csv' });
    a.click();
    notify('Audit log downloaded', 'success', 'audit-log.csv');
  };
  return (
    <div className="page">
      <PageHeader title="Audit log" description="Every change is chained to the one before it, so edits to the log itself can be detected.">
        <Button icon={checking ? <Spinner size="tiny" /> : <ShieldCheckmarkRegular />} disabled={checking || (!server && audit.some((a) => !a.hash))} onClick={check}>Verify log</Button>
        <Button icon={<ArrowDownloadRegular />} onClick={exportCsv}>Download</Button>
      </PageHeader>
      {checked ? (checked.broken === null
        ? <MessageBar intent="success" layout="multiline"><MessageBarBody><MessageBarTitle>Log verified</MessageBarTitle>{`Checked at ${checked.at}. All ${checked.total} entries are intact.`}</MessageBarBody></MessageBar>
        : <MessageBar intent="error" layout="multiline"><MessageBarBody><MessageBarTitle>The log has been changed</MessageBarTitle>{`${checked.broken} doesn't match its hash. Tell your security team.`}</MessageBarBody></MessageBar>)
        : offline && <MessageBar intent="info" layout="multiline"><MessageBarBody>{offline}</MessageBarBody></MessageBar>}
      <Field label="Search the log" className="narrow-field"><SearchBox value={q} onChange={(_, d) => setQ(d.value)} placeholder="Person, action, or document" /></Field>
      {rows.length === 0 ? (
        <EmptyState picto="ledger" title={q ? 'No entries match' : 'No entries yet'} body={q ? 'Try another name or action.' : 'Entries appear as documents are read, reviewed and exported.'}>
          {q && <Button appearance="primary" onClick={() => setQ('')}>Clear search</Button>}
        </EmptyState>
      ) : (
        <div className="table-card">
          <RTable layout="auto" aria-label="Audit entries">
            <RHeader><RRow><RHead>Time</RHead><RHead>Who</RHead><RHead>Action</RHead><RHead>On</RHead><RHead>Hash</RHead></RRow></RHeader>
            <RBody>
              {paged.items.map((a) => (
                <RRow key={a.key}>
                  <RCell data-label="Time">{a.at}</RCell>
                  <RCell data-label="Who">{a.actor}</RCell>
                  <RCell data-label="Action">{a.action}</RCell>
                  <RCell data-label="On">{a.target}</RCell>
                  <RCell data-label="Hash"><Tooltip content={a.hash} relationship="description"><Text font="monospace">{a.hash.slice(0, 12)}</Text></Tooltip></RCell>
                </RRow>
              ))}
            </RBody>
          </RTable>
          {paged.pager}
          {server?.more && <Button appearance="subtle" onClick={more}>Load older entries</Button>}
        </div>
      )}
    </div>
  );
}

type Component = { name: string; engine: string; current: string; latest: string; notes?: string };
const toComponent = (p: EnginePackage): Component => ({
  name: p.name, engine: p.role ?? '', current: p.current ?? 'Not installed', latest: p.latest ?? p.current ?? 'Unknown',
  notes: p.note || (p.error && `Couldn't check for a newer version (${p.error})`) || undefined,
});

/** Engine versions from the service (PyPI checked every 6 hours); installs run there, in the background, and need a restart. */
export function Updates() {
  const { notify, runs, log } = useApp();
  const [report, setReport] = useState<UpdatesReport | null>(null);
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const load = (refresh = false) => service.updates(refresh).then((r) => { setReport(r); setError(''); return r; }).catch((e: Error) => { setError(e.message); return null; });
  useEffect(() => { void load(); }, []);
  // while an install runs, its outcome is polled; the versions change only after a restart
  const installing = !!report?.job.running;
  useEffect(() => {
    if (!installing) return;
    const t = setInterval(() => {
      void load().then((r) => {
        if (r && !r.job.running) notify(r.job.error ? "The updates didn't install" : 'Updates installed', r.job.error ? 'error' : 'success', r.job.error || 'Restart the app to use them.');
      });
    }, 3000);
    return () => clearInterval(t);
  }, [installing]); // eslint-disable-line react-hooks/exhaustive-deps
  const busy = runs.some((r) => r.status === 'running');
  const items = (report?.packages ?? []).map(toComponent);
  const pending = (report?.packages ?? []).filter((u) => u.update_available).map((u) => u.name);
  const check = async () => {
    setChecking(true);
    const r = await load(true);
    setChecking(false);
    if (r) notify('Checked for updates', 'info', r.packages.some((u) => u.update_available) ? `${r.packages.filter((u) => u.update_available).length} available` : 'Everything is up to date');
  };
  const apply = async (names: string[]) => {
    try {
      await service.install(names);
      log('Started engine updates', names.join(', '));
      notify(`Installing ${count(names.length, 'update', 'updates')}`, 'info', 'Restart the app when it finishes.');
      void load();
    } catch (e) {
      notify("The updates didn't start", 'error', (e as Error).message);
    }
  };
  const setAuto = async (on: boolean) => {
    try {
      await service.autoUpdate(on);
      log(`Turned automatic updates ${on ? 'on' : 'off'}`, 'Engines');
      notify(on ? 'Automatic updates on' : 'Automatic updates off', 'info');
      void load();
    } catch (e) {
      notify("The setting didn't change", 'error', (e as Error).message);
    }
  };

  return (
    <div className="page">
      <PageHeader title="Updates" description="New versions of the engines. Updates wait while a batch is running.">
        <Button appearance="primary" disabled={!pending.length || busy || installing} onClick={() => apply(pending)}>{`Install ${pending.length || ''} updates`.replace('  ', ' ')}</Button>
        <Button icon={checking ? <Spinner size="tiny" /> : <ArrowSyncRegular />} disabled={checking || !report} onClick={check}>{checking ? 'Checking\u00a0…' : 'Check now'}</Button>
      </PageHeader>
      {error ? <MessageBar intent="error" layout="multiline"><MessageBarBody>{`The service can't be reached: ${error}`}</MessageBarBody></MessageBar>
        : installing ? <MessageBar intent="info" layout="multiline"><MessageBarBody>{`Installing ${report?.job.packages.join(', ')}. Restart the app when it finishes.`}</MessageBarBody></MessageBar>
        : busy && <MessageBar intent="info" layout="multiline"><MessageBarBody>Updates are paused until the current batch finishes.</MessageBarBody></MessageBar>}
      {!report && !error && <Spinner label={'Loading\u00a0…'} />}
      {report && <Switch label="Install updates automatically at the next restart" checked={report.auto_update} onChange={(_, d) => setAuto(d.checked)} />}
      {report && pending.length === 0 && <EmptyState compact picto="check_mark_button" title="Everything is up to date" body="We check for new versions every 6 hours." />}
      {report && (
        <div className="table-card">
          <RTable layout="auto" aria-label="Engine components">
            <RHeader><RRow><RHead>Component</RHead><RHead>Used for</RHead><RHead>Installed</RHead><RHead>Available</RHead><RHead><span className="sr-only">Action</span></RHead></RRow></RHeader>
            <RBody>
              {items.map((u) => (
                <RRow key={u.name}>
                  <RCell data-label="Component"><div className="cell-stack"><Text>{u.name}</Text>{u.notes && <Caption1 className="muted">{u.notes}</Caption1>}</div></RCell>
                  <RCell data-label="Used for">{u.engine}</RCell>
                  <RCell data-label="Installed">{u.current}</RCell>
                  <RCell data-label="Available">{pending.includes(u.name) ? u.latest : <StatusBadge status="current" />}</RCell>
                  <RCell>{pending.includes(u.name) && <Button size="small" disabled={busy || installing} onClick={() => apply([u.name])}>Install</Button>}</RCell>
                </RRow>
              ))}
            </RBody>
          </RTable>
        </div>
      )}
    </div>
  );
}
