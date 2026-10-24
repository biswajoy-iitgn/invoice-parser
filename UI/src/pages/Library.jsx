import { useState } from 'react';
import {
  Dropdown, Option, Link, Text, Caption1, Button, ToggleButton, Toolbar, ToolbarDivider, SearchBox, Tooltip,
  Card, DataGrid, DataGridHeader, DataGridRow, DataGridHeaderCell, DataGridBody, DataGridCell, createTableColumn,
} from '@fluentui/react-components';
import { useApp, StatusBadge, EmptyState, num, Title } from '../ui';
import { UploadMenu, FileIcon } from './Home';
import ExportMenu from './Export';

const FILTERS = [['all', 'All'], ['review', 'Needs review'], ['accepted', 'Accepted'], ['failed', 'Failed']];

export default function Library() {
  const { allDocs, docRecord, runs, go, query, setQuery } = useApp();
  const [batch, setBatch] = useState('all');
  const [status, setStatus] = useState('all');
  const [selected, setSelected] = useState(new Set());
  const items = allDocs.filter((d) => !['queued', 'running'].includes(d.status)).map((d) => {
    const r = docRecord(d.id);
    return { ...d, vendor: r.sample?.vendor || '', invoice: r.values.invoice_no || '', total: r.values.gross_total || '' };
  });
  const needle = query.trim().toLowerCase();
  const rows = items.filter((d) => (batch === 'all' || d.run === batch) && (status === 'all' || d.status === status)
    && (!needle || [d.name, d.vendor, d.invoice, d.total, d.run].some((v) => String(v).toLowerCase().includes(needle))));
  const picked = rows.filter((d) => selected.has(d.id));

  const columns = [
    createTableColumn({
      columnId: 'name', compare: (a, b) => a.name.localeCompare(b.name), renderHeaderCell: () => 'Name',
      renderCell: (d) => <div className="name-cell"><FileIcon kind={d.kind} /><Link appearance="subtle" onClick={() => go('review', d.id)}>{d.name}</Link></div>,
    }),
    createTableColumn({ columnId: 'vendor', compare: (a, b) => a.vendor.localeCompare(b.vendor), renderHeaderCell: () => 'Vendor', renderCell: (d) => d.vendor }),
    createTableColumn({ columnId: 'invoice', compare: (a, b) => a.invoice.localeCompare(b.invoice), renderHeaderCell: () => 'Invoice number', renderCell: (d) => d.invoice }),
    createTableColumn({ columnId: 'total', compare: (a, b) => num(a.total) - num(b.total), renderHeaderCell: () => 'Gross total', renderCell: (d) => <span className="num">{d.total}</span> }),
    createTableColumn({ columnId: 'run', compare: (a, b) => a.run.localeCompare(b.run), renderHeaderCell: () => 'Batch', renderCell: (d) => <Link appearance="subtle" onClick={() => go('run', d.run)}>{d.run}</Link> }),
    createTableColumn({ columnId: 'status', compare: (a, b) => a.status.localeCompare(b.status), renderHeaderCell: () => 'Status', renderCell: (d) => <StatusBadge status={d.status} /> }),
  ];

  return (
    <div className="page">
      <div className="titlebar">
        <Title>Documents</Title>
      </div>
      <Toolbar aria-label="Document commands" className="commandbar">
        <UploadMenu primary={false} />
        <Tooltip content={picked.length ? `Export or send ${picked.length} documents` : 'Select documents to export them'} relationship="description">
          <span><ExportMenu scope={{ docs: picked }} label={picked.length ? `Export ${picked.length}` : 'Export'} disabled={!picked.length} /></span>
        </Tooltip>
        <ToolbarDivider />
        <Dropdown appearance="filled-lighter" aria-label="Batch" className="cmd-dropdown"
          value={batch === 'all' ? 'All batches' : runs.find((r) => r.id === batch)?.source} selectedOptions={[batch]} onOptionSelect={(_, d) => setBatch(d.optionValue)}>
          <Option value="all">All batches</Option>
          {runs.map((r) => <Option key={r.id} value={r.id} text={r.source}>{`${r.source}, ${r.started}`}</Option>)}
        </Dropdown>
        {batch !== 'all' && <Button appearance="subtle" onClick={() => go('run', batch)}>Batch details</Button>}
        <div className="cmd-spacer" />
        <SearchBox appearance="filled-lighter" value={query} onChange={(_, d) => setQuery(d.value)} placeholder="Filter by name, vendor, or number" aria-label="Filter documents" className="cmd-search" />
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
      ) : (
        <Card className="list-card">
        <DataGrid items={rows} columns={columns} sortable resizableColumns selectionMode="multiselect" getRowId={(d) => d.id}
          selectedItems={selected} onSelectionChange={(_, d) => setSelected(new Set(d.selectedItems))}
          aria-label="Documents" className="responsive list-table"
          columnSizingOptions={{ name: { idealWidth: 260, minWidth: 180 }, vendor: { idealWidth: 240, minWidth: 140 }, invoice: { idealWidth: 170, minWidth: 120 }, total: { idealWidth: 120, minWidth: 90 }, run: { idealWidth: 90, minWidth: 70 }, status: { idealWidth: 140, minWidth: 120 } }}>
          <DataGridHeader>
            <DataGridRow selectionCell={{ checkboxIndicator: { 'aria-label': 'Select all documents' } }}>
              {({ renderHeaderCell }) => <DataGridHeaderCell>{renderHeaderCell()}</DataGridHeaderCell>}
            </DataGridRow>
          </DataGridHeader>
          <DataGridBody>
            {({ item, rowId }) => (
              <DataGridRow key={rowId} selectionCell={{ checkboxIndicator: { 'aria-label': `Select ${item.name}` } }}>
                {({ renderCell, columnId }) => <DataGridCell data-label={columns.find((c) => c.columnId === columnId).renderHeaderCell()}>{renderCell(item)}</DataGridCell>}
              </DataGridRow>
            )}
          </DataGridBody>
        </DataGrid>
        </Card>
      )}
      <Caption1 className="muted" role="status">{`${rows.length} of ${items.length} documents${picked.length ? `, ${picked.length} selected` : ''}`}</Caption1>
    </div>
  );
}
