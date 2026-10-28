// The Excel model, shared by Documents (extracted data) and Databases: an ARIA grid with one Tab stop, frozen header
// and first columns, a totals row, and in-place editing. Long sheets page instead of rendering every row.
import { useRef, useState } from 'react';
import type { KeyboardEvent, ReactElement, ReactNode } from 'react';
import {
  Button, Caption1, Dropdown, Input, Option, Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Tooltip,
  makeStyles, mergeClasses, shorthands, tableCellClassNames, tableHeaderCellClassNames, tokens,
} from '@fluentui/react-components';
import { ChevronDoubleLeftRegular, ChevronDoubleRightRegular, ChevronLeftRegular, ChevronRightRegular, EyeRegular } from '@fluentui/react-icons';
import { count, money, num } from './format';

export interface SheetCol<R> {
  key: string;
  head: ReactNode;
  num?: boolean;
  sum?: boolean;
  /** The raw value: totals add it up and editing starts from it. */
  value?: (r: R) => string | undefined;
  render: (r: R) => ReactNode;
  /** A value the AI wasn't sure about: tinted and marked until the document is approved. */
  check?: (r: R) => boolean;
  editable?: (r: R) => boolean;
}

const LINE = 'var(--appSheetLine)';
const useSheetStyles = makeStyles({
  table: { borderCollapse: 'separate', borderSpacing: 0, width: 'max-content', minWidth: '100%', tableLayout: 'auto', fontSize: '13px', lineHeight: '20px', outlineStyle: 'none' },
  row: { borderBottomStyle: 'none' },
  bodyRow: { ':hover': { [`& .${tableCellClassNames.root}`]: { backgroundColor: tokens.colorNeutralBackground1Hover } } },
  cell: {
    height: '32px', ...shorthands.padding('4px', '10px'), whiteSpace: 'nowrap', backgroundColor: tokens.colorNeutralBackground1,
    borderInlineEndWidth: '1px', borderInlineEndStyle: 'solid', borderInlineEndColor: LINE, ...shorthands.borderBottom('1px', 'solid', LINE),
    ':focus-visible': { outlineColor: tokens.colorBrandStroke1, outlineStyle: 'solid', outlineWidth: '2px', outlineOffset: '-2px' },
  },
  head: { position: 'sticky', top: 0, zIndex: 2, backgroundColor: 'var(--appSheetHead)', fontWeight: tokens.fontWeightSemibold },
  foot: { position: 'sticky', bottom: 0, zIndex: 2, backgroundColor: 'var(--appSheetHead)', fontWeight: tokens.fontWeightSemibold, ...shorthands.borderTop('1px', 'solid', tokens.colorNeutralStroke1) },
  sl: { position: 'sticky', insetInlineStart: 0, zIndex: 1, width: '56px', minWidth: '56px', textAlign: 'center', color: tokens.colorNeutralForeground3, backgroundColor: 'var(--appSheetHead)' },
  frozen: { position: 'sticky', zIndex: 1 },
  // with two frozen columns the second sits at a fixed offset, so the first has a fixed width
  fixed: { minWidth: '160px', maxWidth: '160px' },
  f0: { insetInlineStart: '56px' },
  f1: { insetInlineStart: '216px' },
  edge: { boxShadow: '4px 0 6px -4px rgba(0, 0, 0, .18)' },
  corner: { zIndex: 3 }, // a frozen cell in the header or footer stays above both
  num: { textAlign: 'end', fontVariantNumeric: 'tabular-nums' },
  numHead: { [`& .${tableHeaderCellClassNames.button}`]: { justifyContent: 'flex-end' } },
  check: { backgroundColor: tokens.colorBrandBackground2 },
  // a changed cell keeps a bar on its leading edge until it is saved (shape, not colour alone)
  edited: { boxShadow: `inset 3px 0 0 ${tokens.colorBrandStroke1}` },
  deleted: { color: tokens.colorNeutralForeground4, textDecorationLine: 'line-through' },
  editing: { ...shorthands.padding('0'), minWidth: '160px' },
  input: { width: '100%', height: '31px', ...shorthands.borderRadius('0') },
  pager: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', columnGap: '4px', rowGap: '8px', paddingBlock: '8px' },
  pagerCount: { marginInlineEnd: 'auto', fontVariantNumeric: 'tabular-nums' },
  pagerSize: { display: 'flex', alignItems: 'center', gap: '8px', marginInlineEnd: '12px' },
  pagerSelect: { minWidth: '80px' },
});

/**
 * Keyboard: arrows move the active cell; Home/End along the row, Ctrl+Home/End to the corners, Page Up/Down by a screenful.
 * On an editable cell, Enter or F2 edits, typing replaces, Delete clears; while editing, Enter saves and moves down,
 * Tab saves and moves right, Escape cancels. On a read-only sheet, Enter opens the row.
 */
export function Sheet<R extends { key: string }>({ label, cols, rows, all = rows, offset = 0, frozen = 1, onOpen, onEdit, onActiveRow, isEdited, isDeleted }: {
  label: string; cols: SheetCol<R>[]; rows: R[]; all?: R[]; offset?: number; frozen?: number;
  onOpen?: (r: R) => void; onEdit?: (r: R, col: string, value: string) => void; onActiveRow?: (r: R | null) => void;
  isEdited?: (r: R, col: string) => boolean; isDeleted?: (r: R) => boolean;
}) {
  const st = useSheetStyles();
  const ref = useRef<HTMLTableElement>(null);
  const [active, setActive] = useState<[number, number]>([1, 1]); // [row, column]; row 0 is the header, column 0 the Sl. no.
  const [editing, setEditing] = useState<{ at: [number, number]; draft: string } | null>(null);
  const totals = cols.some((c) => c.sum);
  const lastRow = rows.length + (totals ? 1 : 0);
  const lastCol = cols.length;
  const rowAt = (r: number) => (r >= 1 && r <= rows.length ? rows[r - 1] : null);
  const colAt = (c: number) => (c >= 1 ? cols[c - 1] : null);
  const canEdit = (r: number, c: number) => {
    const row = rowAt(r), col = colAt(c);
    return !!(onEdit && row && col?.editable?.(row) && !isDeleted?.(row));
  };
  const focus = (to: [number, number]) => {
    setActive(to);
    requestAnimationFrame(() => ref.current?.querySelector<HTMLElement>(`[data-cell="${to[0]}-${to[1]}"]`)?.focus());
  };
  const clamp = (r: number, c: number): [number, number] => [Math.min(lastRow, Math.max(0, r)), Math.min(lastCol, Math.max(0, c))];
  const commit = (move: [number, number] | null) => {
    if (!editing) return;
    const [r, c] = editing.at;
    const row = rowAt(r), col = colAt(c);
    if (row && col && editing.draft !== (col.value?.(row) ?? '')) onEdit?.(row, col.key, editing.draft);
    setEditing(null);
    focus(move ? clamp(r + move[0], c + move[1]) : [r, c]);
  };
  const cell = (r: number, c: number) => ({
    'data-cell': `${r}-${c}`,
    tabIndex: r === active[0] && c === active[1] && !editing ? 0 : -1,
    onFocus: () => { setActive([r, c]); onActiveRow?.(rowAt(r)); },
    onDoubleClick: () => { const row = rowAt(r), col = colAt(c); if (row && col && canEdit(r, c)) setEditing({ at: [r, c], draft: col.value?.(row) ?? '' }); },
  });
  const move = (e: KeyboardEvent<HTMLTableElement>) => {
    if (editing) return;
    const [r, c] = active;
    const row = rowAt(r), col = colAt(c);
    if (canEdit(r, c) && row && col) {
      if (e.key === 'Enter' || e.key === 'F2') { e.preventDefault(); setEditing({ at: [r, c], draft: col.value?.(row) ?? '' }); return; }
      if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); onEdit?.(row, col.key, ''); return; }
      if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); setEditing({ at: [r, c], draft: e.key }); return; }
    }
    if (e.key === 'Enter' && row && onOpen) { e.preventDefault(); onOpen(row); return; }
    const page = Math.max(1, Math.floor((e.currentTarget.parentElement?.clientHeight ?? 320) / 32) - 2);
    const keys: Record<string, [number, number]> = {
      ArrowUp: [r - 1, c], ArrowDown: [r + 1, c], ArrowLeft: [r, c - 1], ArrowRight: [r, c + 1],
      Home: e.ctrlKey ? [0, 0] : [r, 0], End: e.ctrlKey ? [lastRow, lastCol] : [r, lastCol],
      PageUp: [r - page, c], PageDown: [r + page, c],
    };
    const next = keys[e.key];
    if (!next) return;
    e.preventDefault();
    focus(clamp(next[0], next[1]));
  };
  const cls = (c: SheetCol<R>, i: number, ...extra: (string | false | undefined)[]) =>
    mergeClasses(st.cell, c.num && st.num, i < frozen && st.frozen, frozen > 1 && i < frozen && st.fixed, i < frozen && (i === 0 ? st.f0 : st.f1), i === frozen - 1 && st.edge, ...extra);
  return (
    <div className="sheet-wrap">
      <Table ref={ref} className={st.table} role="grid" aria-label={label} aria-rowcount={all.length + 1 + (totals ? 1 : 0)} aria-colcount={lastCol + 1} onKeyDown={move}>
        <TableHeader role="rowgroup">
          <TableRow role="row" aria-rowindex={1} className={st.row}>
            <TableHeaderCell role="columnheader" className={mergeClasses(st.cell, st.sl, st.head, st.corner)} {...cell(0, 0)}>Sl. no.</TableHeaderCell>
            {cols.map((c, i) => <TableHeaderCell key={c.key} role="columnheader" className={cls(c, i, st.head, c.num && st.numHead, i < frozen && st.corner)} {...cell(0, i + 1)}>{c.head}</TableHeaderCell>)}
          </TableRow>
        </TableHeader>
        <TableBody role="rowgroup">
          {rows.map((r, n) => {
            const gone = isDeleted?.(r);
            return (
              <TableRow key={r.key} role="row" aria-rowindex={offset + n + 2} className={mergeClasses(st.row, st.bodyRow)}>
                <TableCell role="rowheader" className={mergeClasses(st.cell, st.sl, gone && st.deleted)} {...cell(n + 1, 0)}>
                  {offset + n + 1}{gone && <span className="sr-only">, deleted when you save</span>}
                </TableCell>
                {cols.map((c, i) => {
                  const check = c.check?.(r);
                  const changed = isEdited?.(r, c.key);
                  const isEditing = !!editing && editing.at[0] === n + 1 && editing.at[1] === i + 1;
                  return (
                    <TableCell key={c.key} role="gridcell" aria-readonly={onEdit ? !c.editable?.(r) : undefined}
                      className={cls(c, i, check && st.check, changed && st.edited, gone && st.deleted, isEditing && st.editing)} {...cell(n + 1, i + 1)}>
                      {isEditing && editing ? (
                        <Input autoFocus className={st.input} value={editing.draft} aria-label={`${typeof c.head === 'string' ? c.head : c.key}, row ${offset + n + 1}`}
                          onChange={(_, d) => setEditing({ ...editing, draft: d.value })}
                          onBlur={() => commit(null)}
                          onKeyDown={(e) => {
                            e.stopPropagation();
                            if (e.key === 'Enter') { e.preventDefault(); commit([1, 0]); }
                            if (e.key === 'Tab') { e.preventDefault(); commit([0, e.shiftKey ? -1 : 1]); }
                            if (e.key === 'Escape') { e.preventDefault(); setEditing(null); focus(editing.at); }
                          }} />
                      ) : (
                        <>
                          {check && <EyeRegular className="check-mark" aria-hidden />}
                          {c.render(r)}
                          {check && <span className="sr-only">, needs a check</span>}
                          {changed && <span className="sr-only">, changed</span>}
                        </>
                      )}
                    </TableCell>
                  );
                })}
              </TableRow>
            );
          })}
        </TableBody>
        {totals && (
          <tfoot>
            <TableRow role="row" aria-rowindex={all.length + 2} className={st.row}>
              <TableCell role="rowheader" className={mergeClasses(st.cell, st.sl, st.foot, st.corner)} {...cell(lastRow, 0)} />
              {cols.map((c, i) => (
                <TableCell key={c.key} role="gridcell" className={cls(c, i, st.foot, i < frozen && st.corner)} {...cell(lastRow, i + 1)}>
                  {i === 0 ? (all.length > rows.length ? `Total, all ${all.length} rows` : 'Total') : c.sum ? money(all.reduce((a, r) => a + num(c.value?.(r)), 0)) : ''}
                </TableCell>
              ))}
            </TableRow>
          </tfoot>
        )}
      </Table>
    </div>
  );
}

const SIZES = [25, 50, 100];
/** Pages a long list. The page resets to the first whenever `resetKey` (filters, search, sheet) changes. */
export function usePaging<T>(items: T[], resetKey: string, label = 'rows') {
  const [at, setAt] = useState({ key: resetKey, page: 0 });
  const [size, setSize] = useState(50);
  const pages = Math.max(1, Math.ceil(items.length / size));
  const page = at.key === resetKey ? Math.min(at.page, pages - 1) : 0;
  const offset = page * size;
  const goTo = (p: number) => setAt({ key: resetKey, page: Math.max(0, Math.min(pages - 1, p)) });
  return {
    items: items.slice(offset, offset + size),
    offset,
    pager: items.length > SIZES[0] ? (
      <Pager label={label} total={items.length} offset={offset} shown={Math.min(size, items.length - offset)} page={page} pages={pages} size={size}
        onPage={goTo} onSize={(s) => { setSize(s); setAt({ key: resetKey, page: 0 }); }} />
    ) : null,
  };
}

function Pager({ label, total, offset, shown, page, pages, size, onPage, onSize }: {
  label: string; total: number; offset: number; shown: number; page: number; pages: number; size: number; onPage: (p: number) => void; onSize: (s: number) => void;
}) {
  const st = useSheetStyles();
  const btn = (tip: string, icon: ReactElement, to: number, off: boolean) => (
    <Tooltip content={tip} relationship="label"><Button appearance="subtle" icon={icon} disabled={off} onClick={() => onPage(to)} /></Tooltip>
  );
  return (
    <nav className={st.pager} aria-label={`Pages of ${label}`}>
      <Caption1 className={st.pagerCount} aria-live="polite">{`${offset + 1} to ${offset + shown} of ${count(total, label.replace(/s$/, ''), label)}`}</Caption1>
      <span className={st.pagerSize}>
        <Caption1 id={`size-${label}`}>Rows per page</Caption1>
        <Dropdown className={st.pagerSelect} aria-labelledby={`size-${label}`} value={String(size)} selectedOptions={[String(size)]} onOptionSelect={(_, d) => onSize(Number(d.optionValue))}>
          {SIZES.map((s) => <Option key={s} value={String(s)}>{String(s)}</Option>)}
        </Dropdown>
      </span>
      {btn('First page', <ChevronDoubleLeftRegular className="flip-rtl" />, 0, page === 0)}
      {btn('Previous page', <ChevronLeftRegular className="flip-rtl" />, page - 1, page === 0)}
      <Caption1 className="nowrap">{`Page ${page + 1} of ${pages}`}</Caption1>
      {btn('Next page', <ChevronRightRegular className="flip-rtl" />, page + 1, page >= pages - 1)}
      {btn('Last page', <ChevronDoubleRightRegular className="flip-rtl" />, pages - 1, page >= pages - 1)}
    </nav>
  );
}
