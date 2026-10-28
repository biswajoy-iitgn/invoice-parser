import { useEffect, useState } from 'react';
import {
  Button, Dropdown, Option, Field, TabList, Tab, Input, MessageBar, MessageBarBody, MessageBarTitle, MessageBarActions, Caption1, Spinner,
  Toolbar, ToolbarButton, ToolbarDivider, Tooltip,
  Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
} from '@fluentui/react-components';
import {
  AddRegular, PlugConnectedRegular, DeleteRegular, ArrowUndoRegular, ArrowClockwiseRegular, KeyRegular, LinkRegular,
} from '@fluentui/react-icons';
import { useApp, PageHeader, StatusBadge, EmptyState, RHead, RHeader, RBody, RRow, RCell, RTable, count, useUrlState } from '../ui';
import { Sheet, usePaging } from '../sheet';
import type { SheetCol } from '../sheet';
import { SchemaDiagram } from '../schema';
import { service } from '../api';
import type { Connection, TableDef } from '../types';

const PORTS: Record<string, string> = { PostgreSQL: '5432', 'SQL Server': '1433', MySQL: '3306', Oracle: '1521' };
const NUMERIC = /int|serial|numeric|decimal|float|double|real|money/i;
/** One row of the sheet: its position in the table and its cells. */
interface Row { key: string; i: number; cells: string[] }
type View = 'data' | 'schema' | 'connections';

type Live = { conn: Connection; tables: Record<string, TableDef> };
/** The service's database as the workbook shows it: columns and links from its schema, rows loaded when a table is opened. */
function liveTables(sc: Awaited<ReturnType<typeof service.dbSchema>>): Record<string, TableDef> {
  const fk = new Map(sc.links.map((l) => [`${l.table}.${l.column}`, l.ref_table]));
  return Object.fromEntries(sc.tables.map((t) => [t.name, {
    // ponytail: "id" is taken for the key (schema reports no primary keys); every table here keys on id
    columns: t.columns.map((c) => [c.name, c.type, fk.has(`${t.name}.${c.name}`) ? `FK ${fk.get(`${t.name}.${c.name}`)}` : c.name === 'id' ? 'PK' : ''] as [string, string, string]),
    rows: [], count: t.rows, loaded: false,
  }]));
}

// A database is a workbook: a sheet per table (tabs at the foot, as in Excel), edited in place and saved together.
// Schema shows how the tables relate. With the service reachable it shows the service's own database, read-only (extraction
// writes it); without it, the sample workspace.
export default function Data() {
  const app = useApp();
  const { role, notify, profiles, log } = app;
  const admin = role === 'admin';
  const [live, setLive] = useState<Live | 'none' | 'offline' | null>(null);
  const [offlineWhy, setOfflineWhy] = useState('');
  const load = () => service.dbStatus().then(async (st) => {
    if (!st.configured) { setLive('none'); return; }
    if (!st.reachable) { setLive('offline'); setOfflineWhy(st.error ?? ''); return; }
    const conn: Connection = { id: 'live', name: st.organization || 'Extraction database', kind: 'PostgreSQL', host: st.host ?? '', database: '', status: 'connected' };
    setLive({ conn, tables: liveTables(await service.dbSchema()) });
  }).catch(() => setLive(null));          // the service itself can't be reached: the sample workspace stands
  useEffect(() => { void load(); }, []);
  const isLive = live !== null;
  const liveConn = live && typeof live === 'object' ? live : null;
  const connections = isLive ? (liveConn ? [liveConn.conn] : live === 'offline' ? [{ id: 'live', name: 'Extraction database', kind: 'PostgreSQL', host: '', database: '', status: 'offline' as const }] : []) : app.connections;
  const tables = isLive ? { live: liveConn?.tables ?? {} } : app.tables;
  const setTables = isLive ? () => {} : app.setTables;
  const setConnections = app.setConnections;
  const editable = admin && !isLive;
  // Users see only tables a profile exports to, read-only.
  const linked = new Set(profiles.flatMap((p) => p.linked || []));
  const visible = (cid: string) => Object.keys(tables[cid] || {}).filter((n) => admin || linked.has(`${cid}.${n}`));
  const shown = connections.filter((x) => admin || visible(x.id).length);
  const [dbParam, setDb] = useUrlState<string>('db', shown[0]?.id ?? '');
  const [viewParam, setView] = useUrlState<View>('view', 'data');
  const [tableParam, setTable] = useUrlState<string>('table', '');
  const c = shown.find((x) => x.id === dbParam) ?? shown[0];
  const names = c ? visible(c.id) : [];
  const name = names.includes(tableParam) ? tableParam : names[0];
  const t: TableDef | null = c && name ? tables[c.id][name] : null;
  const view: View = viewParam === 'connections' && !admin ? 'data' : viewParam;
  const target = `${c?.id}.${name}`;
  // Unsaved edits belong to one table; switching tables starts clean.
  const [pending, setPending] = useState<{ target: string; staged: Record<string, string>; deleted: number[] }>({ target, staged: {}, deleted: [] });
  const { staged, deleted } = pending.target === target ? pending : { staged: {} as Record<string, string>, deleted: [] as number[] };
  const setStaged = (next: Record<string, string>) => setPending({ target, staged: next, deleted });
  const [activeRow, setActiveRow] = useState<Row | null>(null);
  const [adding, setAdding] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const changes = Object.keys(staged).length + deleted.length;
  const rows: Row[] = (t?.rows ?? []).map((cells, i) => ({ key: `${target}-${i}`, i, cells }));
  const paged = usePaging(rows, target, 'rows');

  const pick = (db: string, table?: string) => { setDb(db); setTable(table ?? ''); setActiveRow(null); };
  const commit = () => {
    if (!t || !c || !name) return;
    const next = t.rows.map((r, i) => r.map((v, j) => staged[`${i}:${j}`] ?? v)).filter((_, i) => !deleted.includes(i));
    setTables({ ...tables, [c.id]: { ...tables[c.id], [name]: { ...t, rows: next } } });
    log('Edited database rows', `${c.name}, ${name}, ${count(changes, 'change', 'changes')}`);
    notify(`${count(changes, 'change', 'changes')} saved to ${name}`);
    setPending({ target, staged: {}, deleted: [] });
  };
  const addRow = () => {
    if (!t || !c || !name) return;
    setTables({ ...tables, [c.id]: { ...tables[c.id], [name]: { ...t, rows: [...t.rows, t.columns.map(() => '')] } } });
  };
  const toggleDelete = (r: Row) => setPending({ target, staged, deleted: deleted.includes(r.i) ? deleted.filter((x) => x !== r.i) : [...deleted, r.i] });
  const retry = async () => {
    if (!c) return;
    setRetrying(true);
    if (isLive) await load();
    else await new Promise((res) => setTimeout(res, 1200));
    setRetrying(false);
    log('Tried to reconnect', c.name);
    if (!isLive || live === 'offline') notify(`Still can't reach ${c.name}`, 'error', offlineWhy || `${c.host} didn't answer.`);
  };
  // a live table's rows arrive when it is opened
  useEffect(() => {
    if (!liveConn || !name || liveConn.tables[name]?.loaded) return;
    service.dbTable(name).then((r) => setLive((cur) => (cur && typeof cur === 'object'
      ? { ...cur, tables: { ...cur.tables, [name]: { ...cur.tables[name], rows: r.rows.map((row) => row.map((v) => v ?? '')), loaded: true } } } : cur)))
      .catch((e: Error) => notify(`${name} couldn't be read`, 'error', e.message));
  }, [liveConn, name]); // eslint-disable-line react-hooks/exhaustive-deps

  const cols: SheetCol<Row>[] = (t?.columns ?? []).map(([col, type, key], j) => ({
    key: col,
    head: (
      <span className="col-head">
        {key === 'PK' && <Tooltip content="Key" relationship="label"><KeyRegular className="col-key" /></Tooltip>}
        {key.startsWith('FK ') && <Tooltip content={`References ${key.slice(3)}`} relationship="label"><LinkRegular /></Tooltip>}
        <span>{col}</span><span className="col-type">{type}</span>
      </span>
    ),
    num: NUMERIC.test(type),
    value: (r) => staged[`${r.i}:${j}`] ?? r.cells[j],
    render: (r) => staged[`${r.i}:${j}`] ?? r.cells[j],
    // an existing row's key stays fixed; a new row needs one
    editable: (r) => editable && !(key === 'PK' && r.cells[j]),
  }));

  return (
    <div className="page">
      <PageHeader title="Databases" description={isLive ? 'What the service has stored: documents, extractions, values and the audit trail.' : admin ? 'Browse and edit the databases that documents are exported to.' : 'See what was exported. Ask a sysadmin if something needs changing.'}>
        {admin && <Button icon={<PlugConnectedRegular />} onClick={() => setAdding(true)}>Add database</Button>}
      </PageHeader>

      {live === null && <MessageBar intent="info" layout="multiline"><MessageBarBody>{"The service can't be reached, so this shows the sample workspace."}</MessageBarBody></MessageBar>}
      {!c ? (
        <EmptyState picto="card_index_dividers" title="No databases yet" body={admin ? 'Add the database that documents are exported to.' : 'Nothing has been exported to a database you can see.'}>
          {admin && <Button appearance="primary" onClick={() => setAdding(true)}>Add database</Button>}
        </EmptyState>
      ) : (
        <>
          <div className="tabs-scroll">
            <TabList selectedValue={view} onTabSelect={(_, d) => setView(d.value as View)} aria-label="View">
              <Tab value="data">Data</Tab>
              <Tab value="schema">Schema</Tab>
              {admin && <Tab value="connections">Connections</Tab>}
            </TabList>
          </div>
          {view !== 'connections' && (
            <Toolbar aria-label="Database commands" className="commandbar">
              <Dropdown aria-label="Database" className="cmd-dropdown" value={`${c.name} (${c.kind})`} selectedOptions={[c.id]} onOptionSelect={(_, d) => pick(d.optionValue ?? c.id)}>
                {shown.map((x) => <Option key={x.id} value={x.id} text={`${x.name} (${x.kind})`}>{`${x.name} (${x.kind})${x.status === 'connected' ? '' : x.status === 'offline' ? ', offline' : ', not checked yet'}`}</Option>)}
              </Dropdown>
              {view === 'data' && editable && t && (
                <>
                  <ToolbarDivider />
                  <ToolbarButton icon={<AddRegular />} onClick={addRow}>Add row</ToolbarButton>
                  <ToolbarButton icon={activeRow && deleted.includes(activeRow.i) ? <ArrowUndoRegular /> : <DeleteRegular />} disabledFocusable={!activeRow}
                    onClick={() => activeRow && toggleDelete(activeRow)}>{activeRow && deleted.includes(activeRow.i) ? 'Keep row' : 'Delete row'}</ToolbarButton>
                </>
              )}
              <div className="cmd-spacer" />
              <Caption1 className="muted">{`${c.host}, ${c.database}`}</Caption1>
            </Toolbar>
          )}

          {view === 'connections' ? (
            <div role="tabpanel" aria-label="Connections" className="table-card">
              <RTable layout="auto" aria-label="Connections">
                <RHeader><RRow><RHead>Name</RHead><RHead>Type</RHead><RHead>Host</RHead><RHead>Database</RHead><RHead>Status</RHead></RRow></RHeader>
                <RBody>
                  {connections.map((x) => (
                    <RRow key={x.id}>
                      <RCell data-label="Name">{x.name}</RCell>
                      <RCell data-label="Type">{x.kind}</RCell>
                      <RCell data-label="Host"><Caption1 className="mono">{x.host}</Caption1></RCell>
                      <RCell data-label="Database">{x.database}</RCell>
                      <RCell data-label="Status"><StatusBadge status={x.status} /></RCell>
                    </RRow>
                  ))}
                </RBody>
              </RTable>
            </div>
          ) : c.status === 'offline' ? (
            <EmptyState picto="electric_plug" title={`Can't reach ${c.name}`} body={`${c.host} isn't answering. Check the server, then try again.`}>
              <Button appearance="primary" icon={retrying ? <Spinner size="tiny" /> : <ArrowClockwiseRegular />} disabled={retrying} onClick={retry}>{retrying ? 'Trying …' : 'Try again'}</Button>
            </EmptyState>
          ) : names.length === 0 ? (
            <EmptyState picto="card_index_dividers" title={`${c.name} has no tables yet`} body="Export documents to this database and their tables appear here." />
          ) : view === 'schema' ? (
            <div role="tabpanel" aria-label="Schema">
              <SchemaDiagram tables={Object.fromEntries(names.map((n) => [n, tables[c.id][n]]))} onOpen={(n) => { setTable(n); setView('data'); }} />
            </div>
          ) : t && isLive && !t.loaded ? <Spinner label={'Loading\u00a0…'} /> : t && (
            <div role="tabpanel" aria-label="Data" className="stack">
              {changes > 0 && (
                <MessageBar intent="info">
                  <MessageBarBody><MessageBarTitle>Unsaved changes</MessageBarTitle>{`${count(changes, 'change', 'changes')} to ${name}`}</MessageBarBody>
                  <MessageBarActions>
                    <Button appearance="primary" onClick={commit}>Save</Button>
                    <Button onClick={() => setPending({ target, staged: {}, deleted: [] })}>Discard</Button>
                  </MessageBarActions>
                </MessageBar>
              )}
              <div className="workbook">
                {rows.length ? (
                  <Sheet key={target} label={`${name} table`} cols={cols} rows={paged.items} all={rows} offset={paged.offset} onActiveRow={setActiveRow}
                    onEdit={editable ? (r, col, v) => {
                      const j = t.columns.findIndex(([n]) => n === col);
                      const k = `${r.i}:${j}`;
                      const next = { ...staged };
                      if (v === r.cells[j]) delete next[k]; else next[k] = v;
                      setStaged(next);
                    } : undefined}
                    isEdited={(r, col) => `${r.i}:${t.columns.findIndex(([n]) => n === col)}` in staged} isDeleted={(r) => deleted.includes(r.i)} />
                ) : <EmptyState compact picto="card_index_dividers" title={`${name} is empty`} body={admin ? 'Add a row, or export documents to this table.' : 'Nothing has been exported to this table yet.'} />}
                {paged.pager}
                <div className="sheet-foot">
                  <div className="tabs-scroll">
                    <TabList size="small" selectedValue={name} onTabSelect={(_, d) => pick(c.id, String(d.value))} aria-label="Tables" className="sheet-tabs">
                      {names.map((n) => <Tab key={n} value={n}>{`${n} (${tables[c.id][n].count ?? tables[c.id][n].rows.length})`}</Tab>)}
                    </TabList>
                  </div>
                  {editable ? <Caption1 className="muted">Double-click, Enter or start typing to edit a cell</Caption1>
                    : isLive && t && (t.count ?? 0) > t.rows.length && <Caption1 className="muted">{`Newest ${t.rows.length} of ${t.count} rows`}</Caption1>}
                </div>
              </div>
            </div>
          )}
        </>
      )}

      <ConnectionDialog open={adding} setOpen={setAdding} live={isLive} onSaved={(note) => { log('Connected the database', 'Settings'); notify('Database saved', 'success', note); void load(); }} onAdd={(x) => {
        setConnections([...connections, x]);
        setTables({ ...tables, [x.id]: {} });
        pick(x.id);
        log('Added database', `${x.name}, ${x.host}`);
        notify(`${x.name} added`, 'success', 'Test the connection before exporting to it.');
      }} />
    </div>
  );
}

/** With the service, the connection is tested and saved there (.env.local, readable by the service only) and the password never
 * comes back; the service stores to PostgreSQL. Without it, the sample workspace gains a connection and keeps no password. */
function ConnectionDialog({ open, setOpen, onAdd, live, onSaved }: { open: boolean; setOpen: (open: boolean) => void; onAdd: (c: Connection) => void; live: boolean; onSaved: (note: string) => void }) {
  const [f, setF] = useState({ name: '', kind: 'PostgreSQL', host: '', port: '5432', database: '', user: '', password: '', org: '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const set = (k: keyof typeof f) => (_: unknown, d: { value: string }) => setF({ ...f, [k]: d.value });
  const ok = f.name && f.host && f.database && (!live || f.kind === 'PostgreSQL');
  const submit = async () => {
    if (!live) { onAdd({ id: `c-${Date.now()}`, name: f.name, kind: f.kind, host: `${f.host}:${f.port}`, database: f.database, status: 'untested' }); setOpen(false); return; }
    setSaving(true); setError('');
    const auth = f.user ? `${encodeURIComponent(f.user)}${f.password ? `:${encodeURIComponent(f.password)}` : ''}@` : '';
    try {
      const r = await service.dbConfigure(`postgresql://${auth}${f.host}:${f.port}/${encodeURIComponent(f.database)}`, f.org.trim());
      setF({ ...f, password: '' });
      setOpen(false);
      onSaved(r.note ?? 'Restart the app to start storing extractions there.');
    } catch (e) { setError((e as Error).message); }
    setSaving(false);
  };
  return (
    <Dialog open={open} onOpenChange={(_, d) => setOpen(d.open)}>
      <DialogSurface>
        <form onSubmit={(e) => { e.preventDefault(); if (ok) void submit(); }}>
          <DialogBody>
            <DialogTitle>Add a database</DialogTitle>
            <DialogContent className="form">
              <Field label="Name" required><Input value={f.name} onChange={set('name')} placeholder="For example, Finance DB" /></Field>
              <Field label="Type">
                <Dropdown value={f.kind} selectedOptions={[f.kind]} onOptionSelect={(_, d) => { const kind = d.optionValue ?? f.kind; setF({ ...f, kind, port: PORTS[kind] }); }}>
                  {Object.keys(PORTS).map((k) => <Option key={k}>{k}</Option>)}
                </Dropdown>
              </Field>
              <div className="form-grid">
                <Field label="Host" required><Input value={f.host} onChange={set('host')} /></Field>
                <Field label="Port"><Input value={f.port} onChange={set('port')} inputMode="numeric" /></Field>
              </div>
              <Field label="Database name" required><Input value={f.database} onChange={set('database')} /></Field>
              <div className="form-grid">
                <Field label="User name"><Input value={f.user} onChange={set('user')} autoComplete="off" /></Field>
                <Field label="Password" hint={live ? 'Kept by the service, never shown again' : 'Not kept in the sample workspace'}><Input type="password" value={f.password} onChange={set('password')} autoComplete="new-password" /></Field>
              </div>
              {live && <Field label="Organization ID" hint="Extractions are stored for this organization. Leave empty to read without storing."><Input value={f.org} onChange={set('org')} autoComplete="off" /></Field>}
              {live && f.kind !== 'PostgreSQL' && <MessageBar intent="warning" layout="multiline"><MessageBarBody>The service stores to PostgreSQL only.</MessageBarBody></MessageBar>}
              {error && <MessageBar intent="error" layout="multiline"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
            </DialogContent>
            <DialogActions>
              <Button appearance="primary" type="submit" disabled={!ok || saving} icon={saving ? <Spinner size="tiny" /> : undefined}>{live ? 'Test and save' : 'Add'}</Button>
              <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
            </DialogActions>
          </DialogBody>
        </form>
      </DialogSurface>
    </Dialog>
  );
}
