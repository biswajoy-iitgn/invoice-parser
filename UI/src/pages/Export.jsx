import { useState } from 'react';
import {
  Menu, MenuTrigger, MenuPopover, MenuList, MenuItem, MenuButton, MenuDivider, MenuGroup, MenuGroupHeader,
  Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
  Button, Field, Dropdown, Option, RadioGroup, Radio, Body1,
} from '@fluentui/react-components';
import { ArrowExportRegular, DocumentTableRegular, DocumentTextRegular, DatabaseArrowUpRegular, FlowRegular } from '@fluentui/react-icons';
import { useApp } from '../ui';
import { CONNECTIONS, DOCS } from '../data';

const download = (name, type, text) => {
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};
const esc = (s) => String(s ?? '').replace(/[<>&"]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

// scope: a batch ({docs}) or one document under review ({docs, values, rows}).
export default function ExportMenu({ scope, label: buttonLabel = 'Export', appearance = 'subtle', disabled = false }) {
  const { workflows, setWorkflows, tables, setTables, docRecord, notify, go } = useApp();
  const [dbOpen, setDbOpen] = useState(false);
  const [conn, setConn] = useState('fin');
  const [mode, setMode] = useState('upsert');
  const label = scope.docs.length === 1 ? scope.docs[0].name : `${scope.docs.length} documents`;
  const base = label.replace(/\.\w+$/, '').replace(/\s+/g, '_');

  const rowsOf = () => {
    if (scope.rows) return [['Field', 'Value'], ...scope.rows.map((r) => [r.label, scope.values[r.key] ?? ''])];
    const recs = scope.docs.map((d) => docRecord(d.id)).filter(Boolean);
    return [['Document', 'Status', 'Vendor', 'Invoice number', 'Invoice date', 'Gross total'],
      ...recs.map((r) => [r.name, r.status, r.sample?.vendor || '', r.values.invoice_no || '', r.values.invoice_date || '', r.values.gross_total || ''])];
  };
  const csv = () => {
    download(`${base}.csv`, 'text/csv', rowsOf().map((l) => l.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(',')).join('\n'));
    notify('CSV file downloaded', 'success', `${base}.csv`);
  };
  const excel = () => {
    // SpreadsheetML 2003: opens in Excel without extra libraries.
    const xml = `<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="Extraction"><Table>${
      rowsOf().map((r) => `<Row>${r.map((c) => `<Cell><Data ss:Type="String">${esc(c)}</Data></Cell>`).join('')}</Row>`).join('')}</Table></Worksheet></Workbook>`;
    download(`${base}.xls`, 'application/vnd.ms-excel', xml);
    notify('Excel workbook downloaded', 'success', `${base}.xls`);
  };
  const writeDb = () => {
    const t = tables.fin.invoices;
    const recs = scope.docs.map((d) => docRecord(d.id)).filter((r) => r?.sample);
    const rows = [...t.rows];
    recs.forEach((r) => {
      const row = [r.values.invoice_no, r.sample.vendor, r.values.invoice_date, 'INR', r.values.gross_total, 'exported'];
      const at = rows.findIndex((x) => x[0] === row[0]);
      if (at >= 0 && mode === 'upsert') rows[at] = row; else if (at < 0) rows.push(row);
    });
    setTables({ ...tables, fin: { ...tables.fin, invoices: { ...t, rows } } });
    setDbOpen(false);
    notify(`${recs.length} ${recs.length === 1 ? 'row' : 'rows'} written to invoices`, 'success', 'Finance DB');
  };
  const runWorkflow = (w) => {
    setWorkflows(workflows.map((x) => (x.id === w.id ? { ...x, last: 'Just now' } : x)));
    notify(`${w.name} started`, 'success', label);
  };
  const exportable = scope.docs.filter((d) => DOCS[d.sample || d.id]).length;

  return (
    <>
      <Menu>
        <MenuTrigger disableButtonEnhancement>
          <MenuButton appearance={appearance} icon={<ArrowExportRegular />} disabledFocusable={disabled}>{buttonLabel}</MenuButton>
        </MenuTrigger>
        <MenuPopover>
          <MenuList>
            <MenuItem icon={<DocumentTableRegular />} onClick={excel}>Excel workbook</MenuItem>
            <MenuItem icon={<DocumentTextRegular />} onClick={csv}>CSV file</MenuItem>
            <MenuItem icon={<DatabaseArrowUpRegular />} disabled={!exportable} onClick={() => setDbOpen(true)}>Database…</MenuItem>
            <MenuDivider />
            <MenuGroup>
              <MenuGroupHeader>Run an automation</MenuGroupHeader>
              {workflows.filter((w) => w.enabled).map((w) => (
                <MenuItem key={w.id} icon={<FlowRegular />} onClick={() => runWorkflow(w)}>{w.name}</MenuItem>
              ))}
            </MenuGroup>
          </MenuList>
        </MenuPopover>
      </Menu>

      <Dialog open={dbOpen} onOpenChange={(_, d) => setDbOpen(d.open)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Send to a database</DialogTitle>
            <DialogContent className="form">
              <Body1>{`${exportable} ${exportable === 1 ? 'document' : 'documents'} will be written as rows.`}</Body1>
              <Field label="Database">
                <Dropdown value={CONNECTIONS.find((c) => c.id === conn).name} selectedOptions={[conn]} onOptionSelect={(_, d) => setConn(d.optionValue)}>
                  {CONNECTIONS.filter((c) => c.status === 'connected').map((c) => <Option key={c.id} value={c.id} text={c.name}>{`${c.name} (${c.kind})`}</Option>)}
                </Dropdown>
              </Field>
              <Field label="Table" hint="Set by the Freight invoice profile">
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

