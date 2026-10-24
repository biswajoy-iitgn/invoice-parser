import { useState } from 'react';
import {
  Button, Dropdown, Option, Field, TabList, Tab, Table, TableHeader, TableRow, TableHeaderCell, TableBody, TableCell,
  Input, MessageBar, MessageBarBody, MessageBarTitle, MessageBarActions, Text, Caption1, Tooltip, Tree, TreeItem, TreeItemLayout, Card,
  Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
} from '@fluentui/react-components';
import {
  AddRegular, PlugConnectedRegular, DeleteRegular, ArrowUndoRegular, DatabaseRegular, TableRegular, ArrowClockwiseRegular,
} from '@fluentui/react-icons';
import { useApp, PageHeader, StatusBadge, EmptyState } from '../ui';
import { CONNECTIONS } from '../data';

// A cell shows its value as a button; activating it swaps in an Input. Enter or leaving the cell stages, Escape cancels.
function EditCell({ value, label, edited, onStage }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  if (editing) {
    return (
      <Input autoFocus value={draft} aria-label={label} onChange={(_, d) => setDraft(d.value)}
        onBlur={() => { setEditing(false); if (draft !== value) onStage(draft); }}
        onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') { setDraft(value); setEditing(false); } }} />
    );
  }
  return (
    <Button appearance="transparent" className={`cell-btn${edited ? ' is-edited' : ''}`} aria-label={`${label}: ${value || 'empty'}${edited ? ', edited' : ''}. Edit`}
      onClick={() => { setDraft(value); setEditing(true); }}>{value || ' '}</Button>
  );
}

export default function Data() {
  const { tables, setTables, role, notify, profiles } = useApp();
  const admin = role === 'admin';
  // Users see only tables a profile exports to, read-only.
  const linked = new Set(profiles.flatMap((p) => p.linked || []));
  const visible = (cid) => Object.keys(tables[cid] || {}).filter((n) => admin || linked.has(`${cid}.${n}`));
  const [conns, setConns] = useState(CONNECTIONS);
  const shown = conns.filter((x) => admin || visible(x.id).length);
  const [sel, setSel] = useState({ conn: 'fin', table: 'invoices' });
  const [tab, setTab] = useState('rows');
  const [staged, setStaged] = useState({}); // "row:col" -> value
  const [deleted, setDeleted] = useState(new Set());
  const [adding, setAdding] = useState(false);
  const c = conns.find((x) => x.id === sel.conn);
  const t = sel.table ? tables[sel.conn]?.[sel.table] : null;
  const changes = Object.keys(staged).length + deleted.size;

  const pick = (conn, table) => { setSel({ conn, table }); setStaged({}); setDeleted(new Set()); };
  const toggleDelete = (i) => setDeleted((d) => { const n = new Set(d); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  const commit = () => {
    const rows = t.rows.map((r, i) => r.map((v, j) => staged[`${i}:${j}`] ?? v)).filter((_, i) => !deleted.has(i));
    setTables({ ...tables, [sel.conn]: { ...tables[sel.conn], [sel.table]: { ...t, rows } } });
    setStaged({});
    setDeleted(new Set());
    notify(`${changes} ${changes === 1 ? 'change' : 'changes'} saved to ${sel.table}`);
  };
  const addRow = () => setTables({ ...tables, [sel.conn]: { ...tables[sel.conn], [sel.table]: { ...t, rows: [...t.rows, t.columns.map(() => '')] } } });
  const retry = () => {
    setConns(conns.map((x) => (x.id === c.id ? { ...x, status: 'connected' } : x)));
    notify(`Connected to ${c.name}`, 'success', 'It has no tables yet');
  };

  return (
    <div className="page">
      <PageHeader picto="card_index_dividers" title="Databases" description={admin ? 'Browse and edit the databases that documents are exported to.' : 'See what was exported. Ask a sysadmin if something needs changing.'}>
        {admin && <Button icon={<PlugConnectedRegular />} onClick={() => setAdding(true)}>Add database</Button>}
      </PageHeader>

      <div className="split">
        <Card className="card tree-card">
          <Tree aria-label="Databases and tables" defaultOpenItems={shown.map((x) => x.id)}>
            {shown.map((x) => (
              <TreeItem key={x.id} value={x.id} itemType="branch">
                <TreeItemLayout iconBefore={<DatabaseRegular />} aside={x.status === 'offline' ? <StatusBadge status="offline" /> : null}
                  onClick={() => pick(x.id, visible(x.id)[0] || null)}>{x.name}</TreeItemLayout>
                <Tree>
                  {visible(x.id).map((n) => (
                    <TreeItem key={n} value={`${x.id}.${n}`} itemType="leaf" aria-selected={sel.conn === x.id && sel.table === n}>
                      <TreeItemLayout iconBefore={<TableRegular />} className={sel.conn === x.id && sel.table === n ? 'tree-sel' : ''} onClick={() => pick(x.id, n)}>{n}</TreeItemLayout>
                    </TreeItem>
                  ))}
                </Tree>
              </TreeItem>
            ))}
          </Tree>
        </Card>

        <div className="stack">
          {c.status === 'offline' ? (
            <EmptyState picto="electric_plug" title={`Can't reach ${c.name}`} body={`${c.host} isn't answering. Check the server, then try again.`}>
              <Button appearance="primary" icon={<ArrowClockwiseRegular />} onClick={retry}>Try again</Button>
            </EmptyState>
          ) : !t ? (
            <EmptyState picto="card_index_dividers" title={`${c.name} has no tables yet`} body="Export documents to this database and their tables appear here." />
          ) : (
            <>
              <div className="row between">
                <div>
                  <Text as="h2" size={500} weight="semibold">{sel.table}</Text>
                  <Caption1 block className="muted">{`${c.name}, ${c.kind}, ${t.rows.length} rows`}</Caption1>
                </div>
                {admin && tab === 'rows' && <Button icon={<AddRegular />} onClick={addRow}>Add row</Button>}
              </div>
              <div className="tabs-scroll"><TabList selectedValue={tab} onTabSelect={(_, d) => setTab(d.value)}>
                <Tab value="rows">Rows</Tab>
                <Tab value="schema">Columns</Tab>
                {admin && <Tab value="connections">Connections</Tab>}
              </TabList></div>
              {tab === 'rows' && changes > 0 && (
                <MessageBar intent="info">
                  <MessageBarBody><MessageBarTitle>Unsaved changes</MessageBarTitle>{`${changes} ${changes === 1 ? 'change' : 'changes'} to ${sel.table}`}</MessageBarBody>
                  <MessageBarActions>
                    <Button appearance="primary" onClick={commit}>Save</Button>
                    <Button onClick={() => { setStaged({}); setDeleted(new Set()); }}>Discard</Button>
                  </MessageBarActions>
                </MessageBar>
              )}
              {tab === 'rows' && (
                <div role="tabpanel" aria-label="Rows" className="table-card">
                  <Table size="small" aria-label={`${sel.table} rows`} className="responsive data-table">
                    <TableHeader><TableRow>
                      {t.columns.map(([n]) => <TableHeaderCell key={n}>{n}</TableHeaderCell>)}
                      {admin && <TableHeaderCell><span className="sr-only">Delete</span></TableHeaderCell>}
                    </TableRow></TableHeader>
                    <TableBody>
                      {t.rows.map((r, i) => (
                        <TableRow key={i} className={deleted.has(i) ? 'row-deleted' : ''}>
                          {r.map((v, j) => {
                            const k = `${i}:${j}`;
                            const pk = t.columns[j][2] === 'PK';
                            return (
                              <TableCell key={j} data-label={t.columns[j][0]}>
                                {!admin || deleted.has(i) || (pk && v) ? <Text weight={pk ? 'semibold' : 'regular'}>{v}</Text> : (
                                  <EditCell value={staged[k] ?? v} label={t.columns[j][0]} edited={k in staged}
                                    onStage={(nv) => setStaged((s) => { const n = { ...s }; if (nv === v) delete n[k]; else n[k] = nv; return n; })} />
                                )}
                              </TableCell>
                            );
                          })}
                          {admin && (
                            <TableCell>
                              <Tooltip content={deleted.has(i) ? 'Undo delete' : 'Delete row'} relationship="label">
                                <Button appearance="subtle" size="small" icon={deleted.has(i) ? <ArrowUndoRegular /> : <DeleteRegular />} onClick={() => toggleDelete(i)} />
                              </Tooltip>
                            </TableCell>
                          )}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
              {tab === 'schema' && (
                <div role="tabpanel" aria-label="Columns" className="table-card">
                  <Table aria-label={`${sel.table} columns`} className="responsive">
                    <TableHeader><TableRow><TableHeaderCell>Column</TableHeaderCell><TableHeaderCell>Type</TableHeaderCell><TableHeaderCell>Key</TableHeaderCell></TableRow></TableHeader>
                    <TableBody>
                      {t.columns.map(([n, ty, key]) => (
                        <TableRow key={n}>
                          <TableCell data-label="Column"><Text font="monospace">{n}</Text></TableCell>
                          <TableCell data-label="Type">{ty}</TableCell>
                          <TableCell data-label="Key">{key || 'None'}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
              {tab === 'connections' && admin && (
                <div role="tabpanel" aria-label="Connections" className="table-card">
                  <Table aria-label="Connections" className="responsive">
                    <TableHeader><TableRow><TableHeaderCell>Name</TableHeaderCell><TableHeaderCell>Type</TableHeaderCell><TableHeaderCell>Host</TableHeaderCell><TableHeaderCell>Database</TableHeaderCell><TableHeaderCell>Status</TableHeaderCell></TableRow></TableHeader>
                    <TableBody>
                      {conns.map((x) => (
                        <TableRow key={x.id}>
                          <TableCell data-label="Name">{x.name}</TableCell>
                          <TableCell data-label="Type">{x.kind}</TableCell>
                          <TableCell data-label="Host"><Text font="monospace">{x.host}</Text></TableCell>
                          <TableCell data-label="Database">{x.database}</TableCell>
                          <TableCell data-label="Status"><StatusBadge status={x.status} /></TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <ConnectionDialog open={adding} setOpen={setAdding} onAdd={(x) => { setConns([...conns, x]); setTables({ ...tables, [x.id]: {} }); pick(x.id, null); notify(`Connected to ${x.name}`); }} />
    </div>
  );
}

function ConnectionDialog({ open, setOpen, onAdd }) {
  const [f, setF] = useState({ name: '', kind: 'PostgreSQL', host: '', port: '5432', database: '', user: '', password: '' });
  const set = (k) => (_, d) => setF({ ...f, [k]: d.value });
  const ok = f.name && f.host && f.database;
  return (
    <Dialog open={open} onOpenChange={(_, d) => setOpen(d.open)}>
      <DialogSurface>
        <form onSubmit={(e) => { e.preventDefault(); if (!ok) return; onAdd({ id: `c-${Date.now()}`, name: f.name, kind: f.kind, host: `${f.host}:${f.port}`, database: f.database, status: 'connected' }); setOpen(false); }}>
          <DialogBody>
            <DialogTitle>Add a database</DialogTitle>
            <DialogContent className="form">
              <Field label="Name" required><Input value={f.name} onChange={set('name')} placeholder="For example, Finance DB" /></Field>
              <Field label="Type">
                <Dropdown value={f.kind} selectedOptions={[f.kind]} onOptionSelect={(_, d) => setF({ ...f, kind: d.optionValue, port: { PostgreSQL: '5432', 'SQL Server': '1433', MySQL: '3306', Oracle: '1521' }[d.optionValue] })}>
                  {['PostgreSQL', 'SQL Server', 'MySQL', 'Oracle'].map((k) => <Option key={k}>{k}</Option>)}
                </Dropdown>
              </Field>
              <div className="form-grid">
                <Field label="Host" required><Input value={f.host} onChange={set('host')} /></Field>
                <Field label="Port"><Input value={f.port} onChange={set('port')} inputMode="numeric" /></Field>
              </div>
              <Field label="Database name" required><Input value={f.database} onChange={set('database')} /></Field>
              <div className="form-grid">
                <Field label="User name"><Input value={f.user} onChange={set('user')} autoComplete="off" /></Field>
                <Field label="Password" hint="Stored in the server vault"><Input type="password" value={f.password} onChange={set('password')} autoComplete="new-password" /></Field>
              </div>
            </DialogContent>
            <DialogActions>
              <Button appearance="primary" type="submit" disabled={!ok}>Connect</Button>
              <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}
