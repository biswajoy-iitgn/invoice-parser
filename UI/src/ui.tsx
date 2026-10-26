import { Component, createContext, useContext, useEffect, useState } from 'react';
import type { ComponentType, ElementType, ErrorInfo, HTMLAttributes, ReactNode } from 'react';
import {
  Badge, Text, Body1, Card, Button, Table, TableHeader, TableBody, TableRow, TableHeaderCell, TableCell, Link,
  makeStyles, mergeClasses, shorthands, tokens,
} from '@fluentui/react-components';
import type {
  BadgeProps, LinkProps, TableBodyProps, TableCellProps, TableHeaderCellProps, TableHeaderProps, TableProps, TableRowProps,
} from '@fluentui/react-components';
import {
  CheckmarkCircle16Filled, ErrorCircle16Filled, Clock16Regular, ArrowSync16Regular, Eye16Regular, PlugDisconnected16Regular,
} from '@fluentui/react-icons';
import type { FluentIcon } from '@fluentui/react-icons';
import { CHARGE_KEYS, fieldValue } from './data';
import type { AppContextValue, SampleDoc, Values } from './types';
import { useControlStyles } from './styles';

export const AppCtx = createContext<AppContextValue | null>(null);
export const useApp = (): AppContextValue => {
  const app = useContext(AppCtx);
  if (!app) throw new Error('useApp is used outside the app');
  return app;
};

type TitleSize = 'hello' | 'page' | 'doc' | 'empty' | 'section' | 'pane';
type TitleProps = HTMLAttributes<HTMLElement> & { as?: ElementType; size?: TitleSize };
// Harsha brand face (Eurostile Extended) for titles, headers and big numbers; Noto Sans for everything else.
const useTitleStyles = makeStyles({
  hello: { fontSize: '28px', lineHeight: '36px', '@media (max-width: 480px)': { fontSize: '22px', lineHeight: '30px' } },
  page: { fontSize: '24px', lineHeight: '32px' },
  doc: { fontSize: '18px', lineHeight: '30px', paddingTop: '2px' },
  empty: { fontSize: '20px', lineHeight: '28px' },
  section: { fontSize: '16px', lineHeight: '24px' },
  pane: { fontSize: '15px', lineHeight: '22px' },
});
export const Title = ({ as: Tag = 'h1', size = 'page', className, children, ...rest }: TitleProps) => (
  <Tag className={mergeClasses('brand-title', useTitleStyles()[size], className)} {...rest}>{children}</Tag>
);

// A Fluent icon on a soft brand tile (Microsoft 365 app tiles); tone 'is-ok' or 'is-bad' for status.
export const Glyph = ({ icon: I, tone = '' }: { icon: FluentIcon | ComponentType; tone?: string }) => <span className={`glyph ${tone}`.trim()}><I /></span>;

// Count pinned to the icon's top-right corner, ringed in the surface colour so it reads as one object (Teams/Outlook).
// The count is drawn only; the button around it carries the number in its accessible name.
export const IconWithCount = ({ icon, count }: { icon: ReactNode; count: number }) => (
  <span className="icon-count">
    {icon}
    {count > 0 && <span className="count-pip" aria-hidden="true">{count > 99 ? '99+' : count}</span>}
  </span>
);

export function PageHeader({ title, description, children, crumbs }: { title: string; description?: string; children?: ReactNode; crumbs?: ReactNode }) {
  return (
    <header className="page-header">
      <div className="page-heading">
        {crumbs}
        <Title>{title}</Title>
        {description && <Body1 className="muted">{description}</Body1>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </header>
  );
}

// Fluent empty state: pictogram, one-line title, short body, one clear action.
export function EmptyState({ picto, title, body, children, compact }: { picto: string; title: string; body?: string; children?: ReactNode; compact?: boolean }) {
  return (
    <div className={`empty${compact ? ' compact' : ''}`} role="status">
      <img src={`/pictograms/${picto}.png`} alt="" className="picto" draggable={false} />
      <Title as="h2" size={compact ? 'section' : 'empty'}>{title}</Title>
      {body && <Body1 className="muted">{body}</Body1>}
      {children && <div className="row center">{children}</div>}
    </div>
  );
}

export function Section({ title, icon, action, children, className = '' }: { title?: string; icon?: ReactNode; action?: ReactNode; children?: ReactNode; className?: string }) {
  return (
    <Card className={`card ${className}`}>
      {(title || action) && (
        <div className="card-head">
          {icon && <span className="card-icon">{icon}</span>}
          {title && <Title as="h2" size="section">{title}</Title>}
          {action && <div className="card-action">{action}</div>}
        </div>
      )}
      {children}
    </Card>
  );
}

export function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="facts">
      {items.map(([k, v]) => (
        <div key={k}>
          <dt><Text size={200}>{k}</Text></dt>
          <dd><Text weight="semibold">{v}</Text></dd>
        </div>
      ))}
    </dl>
  );
}

const STATUS: Record<string, [BadgeProps['color'], string, FluentIcon]> = {
  accepted: ['success', 'Approved', CheckmarkCircle16Filled],
  pass: ['success', 'Passed', CheckmarkCircle16Filled],
  confirmed: ['success', 'Confirmed', CheckmarkCircle16Filled],
  done: ['success', 'Complete', CheckmarkCircle16Filled],
  current: ['success', 'Up to date', CheckmarkCircle16Filled],
  exported: ['success', 'Exported', CheckmarkCircle16Filled],
  connected: ['success', 'Connected', CheckmarkCircle16Filled],
  review: ['brand', 'Needs review', Eye16Regular],
  failed: ['danger', 'Failed', ErrorCircle16Filled],
  offline: ['danger', 'Offline', PlugDisconnected16Regular],
  fallback: ['informative', 'On fallback', PlugDisconnected16Regular],
  running: ['brand', 'Processing', ArrowSync16Regular],
  queued: ['informative', 'Queued', Clock16Regular],
  untested: ['informative', 'Not checked yet', Clock16Regular],
};
export function StatusBadge({ status }: { status: string }) {
  const [color, label, Icon] = STATUS[status] || ['informative', status, null];
  return <Badge appearance="tint" color={color} shape="rounded" icon={Icon ? <Icon /> : undefined}>{label}</Badge>;
}

import { num, money } from './format';
export { LOCALE, pct, num, money, count } from './format';

// ISO 6346 container check digit.
export function iso6346(code: string | undefined): boolean {
  const m = /^([A-Z]{4})(\d{6})(\d)$/.exec(code || '');
  if (!m) return false;
  const v = (c: string) => { const n = c.charCodeAt(0) - 55; return n + Math.floor((n - 1) / 10); }; // A=10, skip multiples of 11
  const sum = [...m[1]].map(v).concat([...m[2]].map(Number)).reduce((a, x, i) => a + x * 2 ** i, 0);
  return (sum % 11) % 10 === Number(m[3]);
}

export interface Check { name: string; result: 'pass' | 'review'; detail: string }
// Checks run on the values as currently shown, so edits re-check live.
export function docChecks(doc: SampleDoc, values: Values): Check[] {
  const get = (k: string): string => values?.[k] ?? fieldValue(doc, k);
  const out: Check[] = [];
  const net = num(get('total')), gross = num(get('gross_total'));
  if (get('gross_total')) {
    const taxed = net + num(get('cgst')) + num(get('sgst'));
    out.push({ name: 'Tax sum', result: Math.abs(taxed - gross) <= 1 ? 'pass' : 'review', detail: `Net + CGST + SGST = ${money(taxed)}, gross ${money(gross)}` });
  }
  const charges = CHARGE_KEYS.filter((k) => get(k));
  if (charges.length && net) {
    const sum = charges.reduce((a, k) => a + num(get(k)), 0);
    out.push({ name: 'Charges add up', result: Math.abs(sum - net) <= 1 ? 'pass' : 'review', detail: `${charges.length} charges = ${money(sum)}, net total ${money(net)}` });
  }
  if (get('container')) out.push({ name: 'Container number', result: iso6346(get('container')) ? 'pass' : 'review', detail: `${get('container')}, ISO 6346 check digit` });
  if (get('vendor_gstin')) out.push({ name: 'GSTIN format', result: /^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/.test(get('vendor_gstin')) ? 'pass' : 'review', detail: get('vendor_gstin') });
  return out;
}

// Tables that reflow into cards on narrow screens (Fluent breakpoint 900 px, 400 % zoom at 341 px): the header hides
// visually, each row becomes a card, each cell shows its column name above or beside its value. Their roles are spelled out,
// because changing a table's CSS display makes Safari and some screen readers drop native table semantics.
const NARROW = '@media (max-width: 900px)';
const PHONE = '@media (max-width: 480px)';
const useTableStyles = makeStyles({
  table: { [NARROW]: { display: 'block', width: '100%' } },
  auto: { tableLayout: 'auto' },
  header: { [NARROW]: { position: 'absolute', width: '1px', height: '1px', overflow: 'hidden', clip: 'rect(0 0 0 0)' } },
  body: { [NARROW]: { display: 'block', width: '100%' } },
  row: {
    [NARROW]: {
      display: 'block', width: '100%', height: 'auto', backgroundColor: tokens.colorNeutralBackground1, borderRadius: tokens.borderRadiusLarge,
      boxShadow: tokens.shadow4, ...shorthands.padding('8px', '12px'), marginBottom: '8px',
    },
  },
  // rows of a table that already sits in a card are divided by lines instead of becoming cards of their own
  listRow: { [NARROW]: { boxShadow: 'none', borderRadius: 0, marginBottom: 0, ...shorthands.borderBottom('1px', 'solid', tokens.colorNeutralStroke2) } },
  cell: {
    [NARROW]: {
      display: 'grid', width: '100%', gridTemplateColumns: 'minmax(96px, 36%) minmax(0, 1fr)', gap: '8px', alignItems: 'center',
      height: 'auto', minHeight: '40px', ...shorthands.padding('4px', 0), overflowWrap: 'anywhere',
      '& > *': { justifySelf: 'start' },
      '&[data-label]::before': { content: 'attr(data-label)', color: tokens.colorNeutralForeground3, fontSize: '12px' },
    },
    [PHONE]: { gridTemplateColumns: 'minmax(0, 1fr)', gap: '2px' },
  },
});
const TableKind = createContext<{ list: boolean }>({ list: false });
/** layout="auto" sizes columns to their content; list marks a table that sits inside a card. */
export const RTable = ({ layout, list = false, className, ...props }: TableProps & { layout?: 'auto'; list?: boolean }) => {
  const s = useTableStyles();
  return <TableKind.Provider value={{ list }}><Table role="table" className={mergeClasses(s.table, layout === 'auto' && s.auto, className)} {...props} /></TableKind.Provider>;
};
export const RHeader = ({ className, ...props }: TableHeaderProps) => <TableHeader role="rowgroup" className={mergeClasses(useTableStyles().header, className)} {...props} />;
export const RBody = ({ className, ...props }: TableBodyProps) => <TableBody role="rowgroup" className={mergeClasses(useTableStyles().body, className)} {...props} />;
export const RRow = ({ className, ...props }: TableRowProps) => {
  const s = useTableStyles();
  const { list } = useContext(TableKind);
  return <TableRow role="row" className={mergeClasses(s.row, list && s.listRow, className)} {...props} />;
};
export const RHead = (props: TableHeaderCellProps) => <TableHeaderCell role="columnheader" {...props} />;
export const RCell = ({ className, ...props }: TableCellProps) => <TableCell role="cell" className={mergeClasses(useTableStyles().cell, className)} {...props} />;

/** A link inside a table row: subtle, wraps long names, keeps a 24 px target. */
export const CellLink = ({ className, appearance = 'subtle', ...props }: LinkProps) => (
  <Link appearance={appearance} className={mergeClasses(useControlStyles().cellLink, className)} {...props} />
);

export const useMedia = (query: string): boolean => {
  const [on, setOn] = useState(() => matchMedia(query).matches);
  useEffect(() => {
    const m = matchMedia(query);
    const f = () => setOn(m.matches);
    m.addEventListener('change', f);
    return () => m.removeEventListener('change', f);
  }, [query]);
  return on;
};

// View state lives in the URL (#/library/data?status=review&sheet=charges), so a filtered view can be shared and survives a reload.
// Replaced, not pushed: Back goes to the previous page, not the previous filter.
export const readParam = (key: string): string | null => new URLSearchParams(location.hash.split('?')[1] || '').get(key);
export function writeParam(key: string, value: string | null | undefined, fallback: string) {
  const [path, query = ''] = location.hash.split('?');
  const params = new URLSearchParams(query);
  if (value === fallback || value === '' || value == null) params.delete(key); else params.set(key, value);
  const next = `${path}${params.toString() ? `?${params}` : ''}`;
  if (next !== location.hash) history.replaceState(null, '', next);
}
export function useUrlState<T extends string>(key: string, fallback: T): [T, (next: T) => void] {
  const [value, setValue] = useState<T>(() => (readParam(key) as T | null) ?? fallback);
  return [value, (next) => { setValue(next); writeParam(key, next, fallback); }];
}

// One page failing never takes the app down: the rail, header and other pages keep working.
export class PageBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('Page failed', error, info.componentStack); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="page">
        <EmptyState picto="warning" title="This page ran into a problem" body="Your work on other pages is safe. Reload the page to try again.">
          <Button appearance="primary" onClick={() => location.reload()}>Reload</Button>
        </EmptyState>
      </div>
    );
  }
}
