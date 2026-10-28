import { useState } from 'react';
import {
  Menu, MenuTrigger, MenuPopover, MenuList, MenuItem, MenuButton, MenuDivider, MenuGroup, MenuGroupHeader,
  Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
  Button, Field, Dropdown, Option, RadioGroup, Radio, Body1,
} from '@fluentui/react-components';
import { ArrowExportRegular, DocumentTableRegular, DocumentTextRegular, DatabaseArrowUpRegular, FlowRegular } from '@fluentui/react-icons';
import { useApp, count } from '../ui';
import { CHARGE_KEYS, DOCS, appliesTo } from '../data';
import { xlsx, csv } from '../xlsx';
import type { Cell, XSheet } from '../xlsx';
import type { MenuButtonProps } from '@fluentui/react-components';
import type { DocRecord, FieldType, Profile, Values, Workflow } from '../types';

/** What gets exported: a batch or a selection ({docs}), or one document under review ({docs, values, rows}). */
export interface ExportScope {
  docs: { id: string; name: string; values?: Values; sample?: unknown }[];
  values?: Values;
  rows?: { key: string; label: string; type?: FieldType }[];
}

const STATUS: Record<string, string> = { review: 'Needs review', accepted: 'Approved', exported: 'Exported', failed: 'Failed', queued: 'Queued', running: 'Processing' };
const save = (name: string, blob: Blob) => {
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};
/** A value as its field type: amounts and numbers stay numbers in Excel, dates stay dates. */
export function cellOf(type: FieldType | undefined, v: string | undefined): Cell {
  if (v == null || v === '') return '';
  if (type === 'Amount') { const n = Number(v.replace(/,/g, '')); return Number.isFinite(n) ? { money: n } : v; }
  if (type === 'Number') { const n = Number(v); return Number.isFinite(n) ? n : v; }
  if (type === 'Date') return { date: v };
  return v;
}

/** The same sheets as Documents, Extracted data: one per profile, plus Charges for invoices. */
export function exportSheets(recs: DocRecord[], profiles: Profile[]): XSheet[] {
  const sheets: XSheet[] = [];
  for (const p of profiles) {
    const docs = recs.filter((r) => r.profile === p.id);
    if (!docs.length) continue;
    const fields = p.fields.filter((f) => f.enabled && f.type !== 'Table');
    sheets.push({ name: p.name, rows: [['Document', 'Status', ...fields.map((f) => f.label)], ...docs.map((r) => [r.name, STATUS[r.status] ?? r.status, ...fields.map((f) => cellOf(f.type, r.values[f.key]))])] });
  }
  const orphans = recs.filter((r) => !profiles.some((p) => p.id === r.profile));
  if (orphans.length) sheets.push({ name: 'Other documents', rows: [['Document', 'Status'], ...orphans.map((r) => [r.name, STATUS[r.status] ?? r.status])] });
  const invoice = profiles.find((p) => p.template === 'invoice');
  const charges = recs.filter((r) => r.profile === invoice?.id).flatMap((r) => CHARGE_KEYS.filter((k) => Number(r.values[k])).map((k): Cell[] => [
    invoice?.fields.find((f) => f.key === k)?.label ?? k, r.values.invoice_no, r.values.vendor, { date: r.values.invoice_date }, cellOf('Amount', r.values[k]), r.name,
  ]));
  if (charges.length) sheets.push({ name: 'Charges', rows: [['Category', 'Invoice no.', 'Vendor name', 'Invoice date', 'Amount (INR)', 'Document'], ...charges] });
  return sheets;
}

export default function ExportMenu({ scope, label: buttonLabel = 'Export', appearance = 'subtle', disabled = false }:
  { scope: ExportScope; label?: string; appearance?: MenuButtonProps['appearance']; disabled?: boolean }) {
  const { workflows, setWorkflows, tables, setTables, docRecord, notify, go, profiles, connections, role, log } = useApp();
  const [dbOpen, setDbOpen] = useState(false);
  const [conn, setConn] = useState('fin');
  const [mode, setMode] = useState('upsert');
  const [running, setRunning] = useState<Workflow | null>(null);
  const label = scope.docs.length === 1 ? scope.docs[0].name : count(scope.docs.length, 'document', 'documents');
  const base = label.replace(/\.\w+$/, '').replace(/\s+/g, '_');
  const recs = () => scope.docs.map((d) => docRecord(d.id)).filter((r): r is DocRecord => !!r);

  const sheets = (): XSheet[] => (scope.rows
    ? [{ name: label, rows: [['Field', 'Value'], ...scope.rows.map((r) => [r.label, cellOf(r.type, scope.values?.[r.key])])] }]
    : exportSheets(recs(), profiles));
  const toCsv = () => {
    const all = sheets().filter((x) => x.name !== 'Charges');
    // one table: with several profiles, their columns side by side
    const heads = [...new Set(all.flatMap((x) => (x.rows[0] as string[]).slice(2)))];
    const rows: Cell[][] = all.length === 1 ? all[0].rows : [['Document', 'Profile', 'Status', ...heads], ...all.flatMap((x) => {
      const h = x.rows[0] as string[];
      return x.rows.slice(1).map((r) => [r[0], x.name, r[1], ...heads.map((k) => (h.indexOf(k) >= 0 ? r[h.indexOf(k)] : ''))]);
    })];
    save(`${base}.csv`, csv(rows));
    log('Exported to CSV', label);
    notify('CSV file downloaded', 'success', `${base}.csv`);
  };
  const toExcel = () => {
    save(`${base}.xlsx`, xlsx(sheets()));
    log('Exported to Excel', label);
    notify('Excel workbook downloaded', 'success', `${base}.xlsx`);
  };
  const invoiceProfile = profiles.find((p) => p.template === 'invoice');
  const writable = recs().filter((r) => r.profile === invoiceProfile?.id && r.values.invoice_no);
  const writeDb = () => {
    const fin = tables.fin;
    const vendors = [...fin.vendors.rows];
    const invoices = [...fin.invoices.rows];
    writable.forEach((r) => {
      let v = vendors.find((x) => x[1] === r.values.vendor);
      if (!v) { v = [String(vendors.length + 1), r.values.vendor || '', r.values.vendor_gstin || '', '']; vendors.push(v); }
      const row = [r.values.invoice_no, v[0], r.values.invoice_date, 'INR', r.values.gross_total, 'exported'];
      const at = invoices.findIndex((x) => x[0] === row[0]);
      if (at >= 0 && mode === 'upsert') invoices[at] = row; else if (at < 0) invoices.push(row);
    });
    setTables({ ...tables, fin: { ...fin, vendors: { ...fin.vendors, rows: vendors }, invoices: { ...fin.invoices, rows: invoices } } });
    setDbOpen(false);
    log('Exported to database', `Finance DB, invoices, ${count(writable.length, 'row', 'rows')}`);
    notify(`${count(writable.length, 'row', 'rows')} written to invoices`, 'success', 'Finance DB');
  };
  const run = (w: Workflow) => {
    setWorkflows(workflows.map((x) => (x.id === w.id ? { ...x, last: 'Just now' } : x)));
    log('Ran automation', `${w.name}, ${label}`);
    notify(`${w.name} started`, 'success', label);
    setRunning(null);
  };
  // Automations write to other systems: sysadmins only, and only those set up for these documents' profiles.
  const docProfiles = new Set(recs().map((r) => r.profile));
  const automations = role === 'admin' ? workflows.filter((w) => w.enabled && [...docProfiles].some((p) => appliesTo(w, p))) : [];
  const hasValues = scope.docs.some((d) => d.values || (typeof d.sample === 'object' ? d.sample : DOCS[(d.sample as string | null) || d.id]));

  return (
    <>
      <Menu>
        <MenuTrigger disableButtonEnhancement>
          <MenuButton appearance={appearance} icon={<ArrowExportRegular />} disabledFocusable={disabled}>{buttonLabel}</MenuButton>
        </MenuTrigger>
        <MenuPopover>
          <MenuList>
            <MenuItem icon={<DocumentTableRegular />} onClick={toExcel}>Excel workbook</MenuItem>
            <MenuItem icon={<DocumentTextRegular />} onClick={toCsv}>CSV file</MenuItem>
            <MenuItem icon={<DatabaseArrowUpRegular />} disabled={!writable.length || !hasValues} onClick={() => setDbOpen(true)}>Database…</MenuItem>
            {automations.length > 0 && (
              <>
                <MenuDivider />
                <MenuGroup>
                  <MenuGroupHeader>Run an automation</MenuGroupHeader>
                  {automations.map((w) => <MenuItem key={w.id} icon={<FlowRegular />} onClick={() => setRunning(w)}>{w.name}</MenuItem>)}
                </MenuGroup>
              </>
            )}
          </MenuList>
        </MenuPopover>
      </Menu>

      <Dialog modalType="alert" open={!!running} onOpenChange={(_, d) => !d.open && setRunning(null)}>
        <DialogSurface>
          {running && (
            <DialogBody>
              <DialogTitle>{`Run ${running.name}?`}</DialogTitle>
              <DialogContent><Body1>{`It sends ${label} to ${running.target}.`}</Body1></DialogContent>
              <DialogActions>
                <Button appearance="primary" onClick={() => run(running)}>Run</Button>
                <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
              </DialogActions>
            </DialogBody>
          )}
        </DialogSurface>
      </Dialog>

      <Dialog open={dbOpen} onOpenChange={(_, d) => setDbOpen(d.open)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Send to a database</DialogTitle>
            <DialogContent className="form">
              <Body1>{`${count(writable.length, 'invoice', 'invoices')} will be written as rows.`}</Body1>
              <Field label="Database">
                <Dropdown value={connections.find((c) => c.id === conn)?.name} selectedOptions={[conn]} onOptionSelect={(_, d) => setConn(d.optionValue ?? conn)}>
                  {connections.filter((c) => c.status === 'connected' && tables[c.id]?.invoices).map((c) => <Option key={c.id} value={c.id} text={c.name}>{`${c.name} (${c.kind})`}</Option>)}
                </Dropdown>
              </Field>
              <Field label="Table" hint={`Set by the ${invoiceProfile?.name ?? 'invoice'} profile`}>
                <Dropdown value="invoices" selectedOptions={['invoices']} disabled><Option value="invoices">invoices</Option></Dropdown>
              </Field>
              <Field label="If the invoice number already exists">
                <RadioGroup value={mode} onChange={(_, d) => setMode(d.value)}>
                  <Radio value="upsert" label="Update the existing row" />
                  <Radio value="insert" label="Skip it" />
                </RadioGroup>
              </Field>
            </DialogContent>
            <DialogActions>
              <Button appearance="primary" onClick={writeDb}>Send</Button>
              <Button onClick={() => { setDbOpen(false); go('data'); }}>Open database</Button>
              <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </>
  );
}
