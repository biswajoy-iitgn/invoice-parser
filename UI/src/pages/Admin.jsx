import { useState } from 'react';
import {
  Button, Table, TableHeader, TableRow, TableHeaderCell, TableBody, TableCell, Dropdown, Option, Field, Input, Switch, Text, Caption1, SearchBox,
  Persona, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger, MessageBar, MessageBarBody, MessageBarTitle,
} from '@fluentui/react-components';
import { PersonAddRegular, ShieldCheckmarkRegular, ArrowDownloadRegular, ArrowSyncRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, StatusBadge, EmptyState } from '../ui';
import { USERS, AUDIT, UPDATES } from '../data';

const ROLES = { user: 'User', admin: 'Sysadmin' };
const GROUPS = ['Finance approvers', 'Stores', 'Procurement'];

export function Users() {
  const { notify } = useApp();
  const [users, setUsers] = useState(USERS);
  const [inv, setInv] = useState(null);
  const set = (id, patch) => setUsers(users.map((u) => (u.id === id ? { ...u, ...patch } : u)));
  const valid = inv && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(inv.email);

  return (
    <div className="page">
      <PageHeader picto="busts_in_silhouette" title="People" description="Users upload and review documents. Sysadmins also manage profiles, engines, and databases.">
        <Button appearance="primary" icon={<PersonAddRegular />} onClick={() => setInv({ email: '', role: 'user' })}>Invite people</Button>
      </PageHeader>
      <div className="table-card">
        <Table aria-label="People" className="responsive">
          <TableHeader><TableRow><TableHeaderCell>Name</TableHeaderCell><TableHeaderCell>Role</TableHeaderCell><TableHeaderCell>Approver groups</TableHeaderCell><TableHeaderCell>Last active</TableHeaderCell></TableRow></TableHeader>
          <TableBody>
            {users.map((u) => (
              <TableRow key={u.id}>
                <TableCell data-label="Name"><Persona name={u.name} secondaryText={u.email} avatar={{ color: 'colorful' }} /></TableCell>
                <TableCell data-label="Role">
                  <Dropdown aria-label={`Role for ${u.name}`} value={ROLES[u.role]} selectedOptions={[u.role]} disabled={u.id === 'u1'}
                    onOptionSelect={(_, d) => { set(u.id, { role: d.optionValue }); notify(`${u.name} is now a ${ROLES[d.optionValue].toLowerCase()}`); }}>
                    {Object.entries(ROLES).map(([k, v]) => <Option key={k} value={k}>{v}</Option>)}
                  </Dropdown>
                  {u.id === 'u1' && <Caption1 block className="muted">You can't change your own role</Caption1>}
                </TableCell>
                <TableCell data-label="Approver groups">
                  <Dropdown aria-label={`Groups for ${u.name}`} multiselect placeholder="None" value={u.groups.join(', ')} selectedOptions={u.groups}
                    onOptionSelect={(_, d) => set(u.id, { groups: d.selectedOptions })}>
                    {GROUPS.map((g) => <Option key={g}>{g}</Option>)}
                  </Dropdown>
                </TableCell>
                <TableCell data-label="Last active">{u.last}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <Dialog open={!!inv} onOpenChange={(_, d) => !d.open && setInv(null)}>
        <DialogSurface>
          {inv && (
            <form onSubmit={(e) => {
              e.preventDefault();
              if (!valid) return;
              setUsers([...users, { id: `u-${Date.now()}`, name: inv.email.split('@')[0], email: inv.email, role: inv.role, groups: [], last: 'Invited' }]);
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
                    <Dropdown value={ROLES[inv.role]} selectedOptions={[inv.role]} onOptionSelect={(_, d) => setInv({ ...inv, role: d.optionValue })}>
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
  const { notify } = useApp();
  const [verified, setVerified] = useState(null);
  const [q, setQ] = useState('');
  const rows = AUDIT.filter((a) => !q || Object.values(a).some((v) => v.toLowerCase().includes(q.toLowerCase())));
  const exportCsv = () => {
    const text = [['Time', 'Who', 'Action', 'On', 'Hash'], ...AUDIT.map((a) => [a.at, a.actor, a.action, a.target, a.hash])].map((r) => r.map((c) => `"${c}"`).join(',')).join('\n');
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: 'audit-log.csv' });
    a.click();
    notify('Audit log downloaded', 'success', 'audit-log.csv');
  };
  return (
    <div className="page">
      <PageHeader picto="ledger" title="Audit log" description="Every change is chained to the one before it, so edits to the log itself can be detected.">
        <Button icon={<ShieldCheckmarkRegular />} onClick={() => { setVerified('24 Oct, 10:05'); notify('Log verified', 'success', `${AUDIT.length} entries, chain intact`); }}>Verify log</Button>
        <Button icon={<ArrowDownloadRegular />} onClick={exportCsv}>Download</Button>
      </PageHeader>
      {verified && <MessageBar intent="success"><MessageBarBody><MessageBarTitle>Log verified</MessageBarTitle>{`Checked at ${verified}. All ${AUDIT.length} entries are intact.`}</MessageBarBody></MessageBar>}
      <Field label="Search the log" className="narrow-field"><SearchBox value={q} onChange={(_, d) => setQ(d.value)} placeholder="Person, action, or document" /></Field>
      {rows.length === 0 ? (
        <EmptyState picto="ledger" title="No entries match" body="Try another name or action.">
          <Button appearance="primary" onClick={() => setQ('')}>Clear search</Button>
        </EmptyState>
      ) : (
        <div className="table-card">
          <Table aria-label="Audit entries" className="responsive">
            <TableHeader><TableRow><TableHeaderCell>Time</TableHeaderCell><TableHeaderCell>Who</TableHeaderCell><TableHeaderCell>Action</TableHeaderCell><TableHeaderCell>On</TableHeaderCell><TableHeaderCell>Hash</TableHeaderCell></TableRow></TableHeader>
            <TableBody>
              {rows.map((a) => (
                <TableRow key={a.hash}>
                  <TableCell data-label="Time">{a.at}</TableCell>
                  <TableCell data-label="Who">{a.actor}</TableCell>
                  <TableCell data-label="Action">{a.action}</TableCell>
                  <TableCell data-label="On">{a.target}</TableCell>
                  <TableCell data-label="Hash"><Text font="monospace">{a.hash}</Text></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

export function Updates() {
  const { notify, runs } = useApp();
  const [items, setItems] = useState(UPDATES);
  const [auto, setAuto] = useState(false);
  const busy = runs.some((r) => r.status === 'running');
  const pending = items.filter((u) => u.current !== u.latest);
  const apply = (names) => { setItems(items.map((u) => (names.includes(u.name) ? { ...u, current: u.latest } : u))); notify(`${names.length} ${names.length === 1 ? 'update' : 'updates'} installed`); };

  return (
    <div className="page">
      <PageHeader picto="counterclockwise_arrows_button" title="Updates" description="New versions of the engines. Updates wait while a batch is running.">
        <Button appearance="primary" disabled={!pending.length || busy} onClick={() => apply(pending.map((u) => u.name))}>{`Install ${pending.length || ''} updates`.replace('  ', ' ')}</Button>
        <Button icon={<ArrowSyncRegular />} onClick={() => notify('Checked for updates', 'info', pending.length ? `${pending.length} available` : 'Everything is up to date')}>Check now</Button>
      </PageHeader>
      {busy && <MessageBar intent="info"><MessageBarBody>Updates are paused until the current batch finishes.</MessageBarBody></MessageBar>}
      <Switch label="Install updates automatically at the next restart" checked={auto} onChange={(_, d) => { setAuto(d.checked); notify(d.checked ? 'Automatic updates on' : 'Automatic updates off', 'info'); }} />
      {pending.length === 0 && <EmptyState compact picto="check_mark_button" title="Everything is up to date" body="We check for new versions every 6 hours." />}
      <div className="table-card">
        <Table aria-label="Engine components" className="responsive">
          <TableHeader><TableRow><TableHeaderCell>Component</TableHeaderCell><TableHeaderCell>Used for</TableHeaderCell><TableHeaderCell>Installed</TableHeaderCell><TableHeaderCell>Available</TableHeaderCell><TableHeaderCell><span className="sr-only">Action</span></TableHeaderCell></TableRow></TableHeader>
          <TableBody>
            {items.map((u) => (
              <TableRow key={u.name}>
                <TableCell data-label="Component"><div className="cell-stack"><Text>{u.name}</Text>{u.current !== u.latest && u.notes && <Caption1 className="muted">{u.notes}</Caption1>}</div></TableCell>
                <TableCell data-label="Used for">{u.engine}</TableCell>
                <TableCell data-label="Installed">{u.current}</TableCell>
                <TableCell data-label="Available">{u.current === u.latest ? <StatusBadge status="current" /> : u.latest}</TableCell>
                <TableCell>{u.current !== u.latest && <Button size="small" disabled={busy} onClick={() => apply([u.name])}>Install</Button>}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
