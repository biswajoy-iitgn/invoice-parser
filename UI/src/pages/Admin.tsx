import { useState } from 'react';
import {
  Button, Dropdown, Option, Field, Input, Switch, Text, Caption1, SearchBox,
  Persona, Tooltip, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger, MessageBar, MessageBarBody, MessageBarTitle, Spinner,
} from '@fluentui/react-components';
import { PersonAddRegular, ShieldCheckmarkRegular, ArrowDownloadRegular, ArrowSyncRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, StatusBadge, EmptyState, RHead, RHeader, RBody, RRow, RCell, RTable, count } from '../ui';
import { usePaging } from '../sheet';
import { verify, stamp } from '../audit';
import { USERS, UPDATES } from '../data';
import type { Role, User } from '../types';

const ROLES: Record<Role, string> = { user: 'User', admin: 'Sysadmin' };
const GROUPS = ['Finance approvers', 'Stores', 'Procurement'];

export function Users() {
  const { notify, log, me } = useApp();
  const [users, setUsers] = useState(USERS);
  const [inv, setInv] = useState<{ email: string; role: Role } | null>(null);
  const set = (id: string, patch: Partial<User>) => setUsers(users.map((u) => (u.id === id ? { ...u, ...patch } : u)));
  const valid = inv && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(inv.email);

  return (
    <div className="page">
      <PageHeader title="People" description="Users upload and review documents. Sysadmins also manage profiles, engines, and databases.">
        <Button appearance="primary" icon={<PersonAddRegular />} onClick={() => setInv({ email: '', role: 'user' })}>Invite people</Button>
      </PageHeader>
      <div className="table-card">
        <RTable layout="auto" aria-label="People">
          <RHeader><RRow><RHead>Name</RHead><RHead>Role</RHead><RHead>Approver groups</RHead><RHead>Last active</RHead></RRow></RHeader>
          <RBody>
            {users.map((u) => (
              <RRow key={u.id}>
                <RCell data-label="Name"><Persona name={u.name} secondaryText={u.email} avatar={{ color: 'brand' }} /></RCell>
                <RCell data-label="Role">
                  <Dropdown aria-label={`Role for ${u.name}`} value={ROLES[u.role]} selectedOptions={[u.role]} disabled={u.id === me.id}
                    onOptionSelect={(_, d) => { const role = d.optionValue as Role; set(u.id, { role }); log('Changed role', `${u.name}, ${ROLES[role]}`); notify(`${u.name} is now a ${ROLES[role].toLowerCase()}`); }}>
                    {Object.entries(ROLES).map(([k, v]) => <Option key={k} value={k}>{v}</Option>)}
                  </Dropdown>
                  {u.id === me.id && <Caption1 block className="muted">{"You can't change your own role"}</Caption1>}
                </RCell>
                <RCell data-label="Approver groups">
                  <Dropdown aria-label={`Groups for ${u.name}`} multiselect placeholder="None" value={u.groups.join(', ')} selectedOptions={u.groups}
                    onOptionSelect={(_, d) => { set(u.id, { groups: d.selectedOptions }); log('Changed approver groups', `${u.name}, ${d.selectedOptions.join(', ') || 'none'}`); }}>
                    {GROUPS.map((g) => <Option key={g}>{g}</Option>)}
                  </Dropdown>
                </RCell>
                <RCell data-label="Last active">{u.last}</RCell>
              </RRow>
            ))}
          </RBody>
        </RTable>
      </div>

      <Dialog open={!!inv} onOpenChange={(_, d) => !d.open && setInv(null)}>
        <DialogSurface>
          {inv && (
            <form onSubmit={(e) => {
              e.preventDefault();
              if (!valid) return;
              setUsers([...users, { id: `u-${Date.now()}`, name: inv.email.split('@')[0], email: inv.email, role: inv.role, groups: [], last: 'Invited' }]);
              log('Invited a person', `${inv.email}, ${ROLES[inv.role]}`);
              notify('Invitation sent', 'success', inv.email);
              setInv(null);
            }}>
              <DialogBody>
                <DialogTitle>Invite people</DialogTitle>
                <DialogContent className="form">
                  <Field label="Email address" required validationState={inv.email && !valid ? 'error' : 'none'} validationMessage={inv.email && !valid ? 'Enter an email address like name@harsha.com' : undefined}>
                    <Input type="email" value={inv.email} onChange={(_, d) => setInv({ ...inv, email: d.value })} />
                  </Field>
                  <Field label="Role">
                    <Dropdown value={ROLES[inv.role]} selectedOptions={[inv.role]} onOptionSelect={(_, d) => setInv({ ...inv, role: d.optionValue as Role })}>
                      {Object.entries(ROLES).map(([k, v]) => <Option key={k} value={k}>{v}</Option>)}
                    </Dropdown>
                  </Field>
                </DialogContent>
                <DialogActions>
                  <Button appearance="primary" type="submit" disabled={!valid}>Send invitation</Button>
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

export function Audit() {
  const { notify, audit } = useApp();
  // The result of the last check: when, how many entries, and the first one whose hash didn't match.
  const [checked, setChecked] = useState<{ at: string; total: number; broken: number } | null>(null);
  const [checking, setChecking] = useState(false);
  const [q, setQ] = useState('');
  const rows = audit.filter((a) => !q || Object.values(a).some((v) => v.toLowerCase().includes(q.toLowerCase())));
  const paged = usePaging(rows, q, 'entries');
  const check = async () => {
    setChecking(true);
    const broken = await verify(audit);
    setChecking(false);
    setChecked({ at: stamp(), total: audit.length, broken });
    notify(broken < 0 ? 'Log verified' : 'The log has been changed', broken < 0 ? 'success' : 'error', broken < 0 ? `${count(audit.length, 'entry', 'entries')}, chain intact` : `First changed entry: ${audit[broken].at}`);
  };
  const exportCsv = () => {
    const text = [['Time', 'Who', 'Action', 'On', 'Hash'], ...audit.map((a) => [a.at, a.actor, a.action, a.target, a.hash])].map((r) => r.map((c) => `"${c.replace(/"/g, '""')}"`).join(',')).join('\n');
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: 'audit-log.csv' });
    a.click();
    notify('Audit log downloaded', 'success', 'audit-log.csv');
  };
  return (
    <div className="page">
      <PageHeader title="Audit log" description="Every change is chained to the one before it, so edits to the log itself can be detected.">
        <Button icon={checking ? <Spinner size="tiny" /> : <ShieldCheckmarkRegular />} disabled={checking || audit.some((a) => !a.hash)} onClick={check}>Verify log</Button>
        <Button icon={<ArrowDownloadRegular />} onClick={exportCsv}>Download</Button>
      </PageHeader>
      {checked && (checked.broken < 0
        ? <MessageBar intent="success"><MessageBarBody><MessageBarTitle>Log verified</MessageBarTitle>{`Checked at ${checked.at}. All ${checked.total} entries are intact.`}</MessageBarBody></MessageBar>
        : <MessageBar intent="error"><MessageBarBody><MessageBarTitle>The log has been changed</MessageBarTitle>{`The entry at ${audit[checked.broken]?.at} doesn't match its hash. Tell your security team.`}</MessageBarBody></MessageBar>)}
      <Field label="Search the log" className="narrow-field"><SearchBox value={q} onChange={(_, d) => setQ(d.value)} placeholder="Person, action, or document" /></Field>
      {rows.length === 0 ? (
        <EmptyState picto="ledger" title="No entries match" body="Try another name or action.">
          <Button appearance="primary" onClick={() => setQ('')}>Clear search</Button>
        </EmptyState>
      ) : (
        <div className="table-card">
          <RTable layout="auto" aria-label="Audit entries">
            <RHeader><RRow><RHead>Time</RHead><RHead>Who</RHead><RHead>Action</RHead><RHead>On</RHead><RHead>Hash</RHead></RRow></RHeader>
            <RBody>
              {paged.items.map((a) => (
                <RRow key={a.hash || a.at + a.action}>
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
        </div>
      )}
    </div>
  );
}

export function Updates() {
  const { notify, runs, log } = useApp();
  const [items, setItems] = useState(UPDATES);
  const [auto, setAuto] = useState(false);
  const [checking, setChecking] = useState(false);
  const busy = runs.some((r) => r.status === 'running');
  const check = () => { setChecking(true); setTimeout(() => { setChecking(false); notify('Checked for updates', 'info', pending.length ? `${pending.length} available` : 'Everything is up to date'); }, 1200); };
  const pending = items.filter((u) => u.current !== u.latest);
  const apply = (names: string[]) => {
    setItems(items.map((u) => (names.includes(u.name) ? { ...u, current: u.latest } : u)));
    log('Installed engine updates', names.join(', '));
    notify(`${count(names.length, 'update', 'updates')} installed`);
  };

  return (
    <div className="page">
      <PageHeader title="Updates" description="New versions of the engines. Updates wait while a batch is running.">
        <Button appearance="primary" disabled={!pending.length || busy} onClick={() => apply(pending.map((u) => u.name))}>{`Install ${pending.length || ''} updates`.replace('  ', ' ')}</Button>
        <Button icon={checking ? <Spinner size="tiny" /> : <ArrowSyncRegular />} disabled={checking} onClick={check}>{checking ? 'Checking\u00a0…' : 'Check now'}</Button>
      </PageHeader>
      {busy && <MessageBar intent="info"><MessageBarBody>Updates are paused until the current batch finishes.</MessageBarBody></MessageBar>}
      <Switch label="Install updates automatically at the next restart" checked={auto} onChange={(_, d) => { setAuto(d.checked); log(`Turned automatic updates ${d.checked ? 'on' : 'off'}`, 'Engines'); notify(d.checked ? 'Automatic updates on' : 'Automatic updates off', 'info'); }} />
      {pending.length === 0 && <EmptyState compact picto="check_mark_button" title="Everything is up to date" body="We check for new versions every 6 hours." />}
      <div className="table-card">
        <RTable layout="auto" aria-label="Engine components">
          <RHeader><RRow><RHead>Component</RHead><RHead>Used for</RHead><RHead>Installed</RHead><RHead>Available</RHead><RHead><span className="sr-only">Action</span></RHead></RRow></RHeader>
          <RBody>
            {items.map((u) => (
              <RRow key={u.name}>
                <RCell data-label="Component"><div className="cell-stack"><Text>{u.name}</Text>{u.current !== u.latest && u.notes && <Caption1 className="muted">{u.notes}</Caption1>}</div></RCell>
                <RCell data-label="Used for">{u.engine}</RCell>
                <RCell data-label="Installed">{u.current}</RCell>
                <RCell data-label="Available">{u.current === u.latest ? <StatusBadge status="current" /> : u.latest}</RCell>
                <RCell>{u.current !== u.latest && <Button size="small" disabled={busy} onClick={() => apply([u.name])}>Install</Button>}</RCell>
              </RRow>
            ))}
          </RBody>
        </RTable>
      </div>
    </div>
  );
}
