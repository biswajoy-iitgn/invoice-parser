import { useEffect, useState } from 'react';
import {
  Dropdown, Option, Caption1, Checkbox, Button, ToggleButton, Toolbar, ToolbarDivider, SearchBox, Tooltip, TabList, Tab,
  Card, DataGrid, DataGridHeader, DataGridRow, DataGridHeaderCell, DataGridBody, DataGridCell, createTableColumn, List, ListItem,
} from '@fluentui/react-components';
import { EyeRegular } from '@fluentui/react-icons';
import { useApp, StatusBadge, EmptyState, num, money, Title, useUrlState, writeParam, useMedia, CellLink, count } from '../ui';
import { Sheet, usePaging } from '../sheet';
import type { SheetCol } from '../sheet';
import { DOCS, CHARGE_KEYS } from '../data';
import { UploadMenu, FileIcon } from './Home';
import ExportMenu from './Export';
import type { Doc, DocKind, DocStatus, Profile, Values } from '../types';

/** A document in the list, with the values shown in its columns. */
type Item = Doc & { values: Values; vendor: string; invoice: string; total: string };
/** One sheet row: a document, or one charge of an invoice (field says which). */
interface Row { key: string; id: string; name: string; kind: DocKind; status: DocStatus; sample?: string | null; values: Values; field?: string }
type Col = SheetCol<Row>;

const FILTERS: [string, string][] = [['all', 'All'], ['review', 'Needs review'], ['accepted', 'Approved'], ['exported', 'Exported'], ['failed', 'Failed']];

// Extracted data as a workbook, like the live Excel export (excel/build_live_workbook.py): one sheet per document type,
// plus Charges with one row per charge. Gridlines, frozen header and first columns, arrow keys move cell to cell, totals at the foot.
const SHEET_LABEL: Record<string, string> = { invoice_no: 'Invoice no.', vendor: 'Vendor name', container: 'Container no.', hbl: 'HBL no.', mbl: 'MBL no.', ex_rate: 'Ex. rate',
  thc: 'THC', bl_fee: 'BL fee', po_no: 'PO no.', dn_no: 'DN no.', dn_date: 'Date', po_ref: 'PO ref.', item_code: 'Item code', unit_price: 'Unit price' };
const amount = (v: string | undefined): string => (v !== '' && v != null && !Number.isNaN(Number(v)) ? money(num(v)) : v ?? '');
// Values the AI wasn't sure about stay marked until the document is approved: an icon and a tint, never colour alone
const toCheck = (d: { status: DocStatus; sample?: string | null; id: string }, key: string, below: number) => d.status === 'review' && (DOCS[d.sample || d.id]?.fields.find((f) => f.key === key)?.conf ?? 1) < below;

function Workbook({ docs, profiles, filters }: { docs: Item[]; profiles: Profile[]; filters: string }) {
  const { go, flagBelow } = useApp();
  const [sheet, setSheet] = useUrlState('sheet', profiles[0].id);
  const open = (r: Row) => go('review', r.id);
  const file: Col = { key: 'file', head: 'Document', render: (r) => <span className="name-cell"><FileIcon kind={r.kind} /><CellLink tabIndex={-1} onClick={() => open(r)}>{r.name}</CellLink></span> };
  const status: Col = { key: 'status', head: 'Status', render: (r) => <StatusBadge status={r.status} /> };
  const fieldCols = (p: Profile): Col[] => p.fields.filter((f) => f.enabled).map((f) => ({
    key: f.key, head: SHEET_LABEL[f.key] || f.label, num: f.type === 'Amount', sum: f.type === 'Amount',
    value: (r) => r.values[f.key], render: (r) => (f.type === 'Amount' ? amount(r.values[f.key]) : r.values[f.key] ?? ''), check: (r) => toCheck(r, f.key, flagBelow),
  }));
  const freight = profiles.find((p) => p.template === 'invoice');
  const invoices = docs.filter((d) => d.profile === freight?.id);
  const charges: Row[] = invoices.flatMap((d) => CHARGE_KEYS.filter((k) => num(d.values[k])).map((k) => ({
    key: `${d.id}-${k}`, id: d.id, name: d.name, kind: d.kind, status: d.status, sample: d.sample, field: k,
    values: { category: freight?.fields.find((f) => f.key === k)?.label || k, invoice_no: d.values.invoice_no, vendor: d.values.vendor, invoice_date: d.values.invoice_date, amount: d.values[k] },
  })));
  const chargeCols: Col[] = [
    { key: 'category', head: 'Category', render: (r) => r.values.category },
    { key: 'invoice', head: 'Invoice no.', render: (r) => r.values.invoice_no },
    { key: 'vendor', head: 'Vendor name', render: (r) => r.values.vendor },
    { key: 'date', head: 'Invoice date', render: (r) => r.values.invoice_date },
    { key: 'amount', head: 'Amount (INR)', num: true, sum: true, value: (r) => r.values.amount, render: (r) => amount(r.values.amount), check: (r) => toCheck(r, r.field ?? '', flagBelow) },
    file,
  ];
  const sheets: { id: string; name: string; rows: Row[]; cols: Col[] }[] = [
    ...profiles.map((p) => ({ id: p.id, name: p.name, rows: docs.filter((d) => d.profile === p.id).map((d) => ({ ...d, key: d.id })), cols: [...fieldCols(p), status, file] })),
    ...(freight ? [{ id: 'charges', name: 'Charges', rows: charges, cols: chargeCols }] : []),
  ];
  const cur = sheets.find((x) => x.id === sheet) || sheets[0];
  const flagged = cur.rows.some((r) => cur.cols.some((c) => c.check?.(r)));
  const paged = usePaging(cur.rows, `${cur.id}|${filters}`, 'rows');
  return (
    <div className="workbook">
      {cur.rows.length ? (
        <Sheet key={cur.id} label={`${cur.name} sheet`} cols={cur.cols} rows={paged.items} all={cur.rows} offset={paged.offset} frozen={cur.id === 'charges' ? 2 : 1} onOpen={open} />
      ) : (
        <EmptyState compact picto="card_index_dividers" title={`No ${cur.name.toLowerCase()} rows`} body="Rows appear here once documents of this kind are read, or when the filters let them through." />
      )}
      {paged.pager}
      <div className="sheet-foot">
        <div className="tabs-scroll">
          <TabList size="small" selectedValue={cur.id} onTabSelect={(_, d) => setSheet(String(d.value))} aria-label="Sheets" className="sheet-tabs">
            {sheets.map((x) => <Tab key={x.id} value={x.id}>{`${x.name} (${x.rows.length})`}</Tab>)}
          </TabList>
        </div>
        {flagged && <Caption1 className="muted check-legend"><EyeRegular className="check-mark" aria-hidden />Marked values still need a check</Caption1>}
      </div>
    </div>
  );
}

export default function Library() {
  const { allDocs, docRecord, runs, go, query, setQuery, route, profiles } = useApp();
  const view = route.id === 'data' ? 'data' : 'files';
  const [batch, setBatch] = useUrlState<string>('batch', 'all');
  const [status, setStatus] = useUrlState<string>('status', 'all');
  useEffect(() => writeParam('q', query, ''), [query]);
  const narrow = useMedia('(max-width: 900px)');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const items = allDocs.filter((d) => !['queued', 'running'].includes(d.status)).map((d): Item => {
    const r = docRecord(d.id) ?? { values: {} as Values };
    return { ...d, values: r.values, vendor: r.values.vendor || r.values.supplier || r.values.consignee || '', invoice: r.values.invoice_no || r.values.po_no || r.values.dn_no || '', total: r.values.gross_total || r.values.po_total || '' };
  });
  const needle = query.trim().toLowerCase();
  const rows = items.filter((d) => (batch === 'all' || d.run === batch) && (status === 'all' || d.status === status)
    && (!needle || [d.name, d.vendor, d.invoice, d.total, d.run].some((v) => String(v).toLowerCase().includes(needle))));
  const picked = rows.filter((d) => selected.has(d.id));
  const filters = `${batch}|${status}|${needle}`;
  const paged = usePaging(rows, `${view}|${filters}`, 'documents');

  const columns = [
    createTableColumn<Item>({
      columnId: 'name', compare: (a, b) => a.name.localeCompare(b.name), renderHeaderCell: () => 'Name',
      renderCell: (d) => <div className="name-cell"><FileIcon kind={d.kind} /><CellLink onClick={() => go('review', d.id)}>{d.name}</CellLink></div>,
    }),
    createTableColumn<Item>({ columnId: 'vendor', compare: (a, b) => a.vendor.localeCompare(b.vendor), renderHeaderCell: () => 'Company', renderCell: (d) => d.vendor }),
    createTableColumn<Item>({ columnId: 'invoice', compare: (a, b) => a.invoice.localeCompare(b.invoice), renderHeaderCell: () => 'Document number', renderCell: (d) => d.invoice }),
    createTableColumn<Item>({ columnId: 'total', compare: (a, b) => num(a.total) - num(b.total), renderHeaderCell: () => 'Total', renderCell: (d) => <span className="num">{d.total && money(num(d.total))}</span> }),
    createTableColumn<Item>({ columnId: 'run', compare: (a, b) => a.run.localeCompare(b.run), renderHeaderCell: () => 'Batch', renderCell: (d) => <CellLink onClick={() => go('run', d.run)}>{d.run}</CellLink> }),
    createTableColumn<Item>({ columnId: 'status', compare: (a, b) => a.status.localeCompare(b.status), renderHeaderCell: () => 'Status', renderCell: (d) => <StatusBadge status={d.status} /> }),
  ];

  return (
    <div className="page">
      <div className="titlebar">
        <Title>Documents</Title>
      </div>
      <div className="tabs-scroll">
        <TabList selectedValue={view} onTabSelect={(_, d) => go('library', d.value === 'data' ? 'data' : undefined)} aria-label="View">
          <Tab value="files">Files</Tab>
          <Tab value="data">Extracted data</Tab>
        </TabList>
      </div>
      <Toolbar aria-label="Document commands" className="commandbar">
        <UploadMenu primary={false} />
        {view === 'files' ? (
          <Tooltip content={picked.length ? `Export or send ${count(picked.length, 'document', 'documents')}` : 'Select documents to export them'} relationship="description">
            <span><ExportMenu scope={{ docs: picked }} label={picked.length ? `Export ${picked.length}` : 'Export'} disabled={!picked.length} /></span>
          </Tooltip>
        ) : (
          <ExportMenu scope={{ docs: rows }} disabled={!rows.length} />
        )}
        <ToolbarDivider />
        <Dropdown aria-label="Batch" className="cmd-dropdown"
          value={batch === 'all' ? 'All batches' : runs.find((r) => r.id === batch)?.source} selectedOptions={[batch]} onOptionSelect={(_, d) => setBatch(d.optionValue ?? 'all')}>
          <Option value="all">All batches</Option>
          {runs.map((r) => <Option key={r.id} value={r.id} text={r.source}>{`${r.source}, ${r.started}`}</Option>)}
        </Dropdown>
        {batch !== 'all' && <Button appearance="subtle" onClick={() => go('run', batch)}>Batch details</Button>}
        <div className="cmd-spacer" />
        <SearchBox value={query} onChange={(_, d) => setQuery(d.value)} placeholder="Filter by name or number" aria-label="Filter documents" className="cmd-search" />
      </Toolbar>
      <div className="pills" role="group" aria-label="Status">
        {FILTERS.map(([k, label]) => (
          <ToggleButton key={k} size="small" shape="circular" checked={status === k} onClick={() => setStatus(k)}>
            {k === 'all' ? label : `${label} (${items.filter((d) => d.status === k).length})`}
          </ToggleButton>
        ))}
      </div>

      {rows.length === 0 ? (
        <EmptyState picto="magnifying_glass_tilted_left" title="No documents match" body="Try a different filter or search.">
          <Button appearance="primary" onClick={() => { setQuery(''); setStatus('all'); setBatch('all'); }}>Clear filters</Button>
        </EmptyState>
      ) : view === 'data' ? (
        <Workbook docs={rows} profiles={profiles} filters={filters} />
      ) : narrow ? (
        <Card className="list-card">
          <List navigationMode="composite" selectionMode="multiselect" selectedItems={[...selected]} aria-label="Documents"
            onSelectionChange={(_, d) => setSelected(new Set(d.selectedItems.map(String)))}>
            {paged.items.map((d) => (
              // a grid row: each part is a gridcell, so screen readers announce the checkbox, name and status as cells
              <ListItem key={d.id} value={d.id} className="file-row" checkmark={null}>
                <div role="gridcell"><Checkbox checked={selected.has(d.id)} aria-label={`Select ${d.name}`}
                  onChange={(_, c) => setSelected((s) => { const n = new Set(s); if (c.checked) n.add(d.id); else n.delete(d.id); return n; })} /></div>
                <div role="gridcell" className="file-row-main">
                  <span className="name-cell"><FileIcon kind={d.kind} /><CellLink onClick={() => go('review', d.id)}>{d.name}</CellLink></span>
                  <Caption1 className="muted">{[d.vendor, d.total && money(num(d.total))].filter(Boolean).join(', ')}</Caption1>
                </div>
                <div role="gridcell"><StatusBadge status={d.status} /></div>
              </ListItem>
            ))}
          </List>
        </Card>
      ) : (
        <Card className="list-card">
        <DataGrid items={paged.items} columns={columns} sortable resizableColumns selectionMode="multiselect" getRowId={(d) => d.id}
          selectedItems={selected} onSelectionChange={(_, d) => setSelected(new Set([...d.selectedItems].map(String)))}
          aria-label="Documents"
          columnSizingOptions={{ name: { idealWidth: 260, minWidth: 180 }, vendor: { idealWidth: 240, minWidth: 140 }, invoice: { idealWidth: 170, minWidth: 120 }, total: { idealWidth: 120, minWidth: 90 }, run: { idealWidth: 90, minWidth: 70 }, status: { idealWidth: 140, minWidth: 120 } }}>
          <DataGridHeader>
            <DataGridRow selectionCell={{ checkboxIndicator: { 'aria-label': 'Select all documents' } }}>
              {({ renderHeaderCell }) => <DataGridHeaderCell>{renderHeaderCell()}</DataGridHeaderCell>}
            </DataGridRow>
          </DataGridHeader>
          <DataGridBody<Item>>
            {({ item, rowId }) => (
              <DataGridRow key={rowId} selectionCell={{ checkboxIndicator: { 'aria-label': `Select ${item.name}` } }}>
                {({ renderCell, columnId }) => <DataGridCell data-label={String(columns.find((c) => c.columnId === columnId)?.renderHeaderCell() ?? '')}>{renderCell(item)}</DataGridCell>}
              </DataGridRow>
            )}
          </DataGridBody>
        </DataGrid>
        </Card>
      )}
      {view === 'files' && paged.pager}
      <Caption1 className="muted" role="status">
        {`${rows.length} of ${count(items.length, 'document', 'documents')}${view !== 'data' && picked.length ? `, ${picked.length} selected` : ''}`}
      </Caption1>
    </div>
  );
}
