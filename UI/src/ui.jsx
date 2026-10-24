import { createContext, useContext } from 'react';
import { Badge, Text, Body1, Card } from '@fluentui/react-components';
import {
  CheckmarkCircle16Filled, ErrorCircle16Filled, Clock16Regular, ArrowSync16Regular, Eye16Regular, PlugDisconnected16Regular,
} from '@fluentui/react-icons';
import { CHARGE_KEYS, fieldValue } from './data';

export const AppCtx = createContext(null);
export const useApp = () => useContext(AppCtx);

// Harsha brand face (Eurostile Extended) for titles, headers and big numbers; Noto Sans for everything else.
export const Title = ({ as = 'h1', size = 'page', className = '', children, ...rest }) => {
  const Tag = as;
  return <Tag className={`brand-title t-${size} ${className}`.trim()} {...rest}>{children}</Tag>;
};

// Fluent Emoji 3D picture (MIT, pinned in public/pictograms/LICENSE.md). Decorative: the text beside it carries the meaning.
export const Picto = ({ name, size = 48, className = '' }) => (
  <img src={`/pictograms/${name}.png`} alt="" width={size} height={size} className={`picto3d ${className}`} draggable={false} />
);

// Count pinned to the icon's top-right corner, ringed in the surface colour so it reads as one object (Teams/Outlook).
export const IconWithCount = ({ icon, count, label }) => (
  <span className="icon-count">
    {icon}
    {count > 0 && <span className="count-pip" aria-label={label}>{count > 99 ? '99+' : count}</span>}
  </span>
);

export function PageHeader({ title, description, children, crumbs, picto }) {
  return (
    <header className="page-header">
      {picto && <Picto name={picto} size={48} className="page-picto" />}
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
export function EmptyState({ picto, title, body, children, compact }) {
  return (
    <div className={`empty${compact ? ' compact' : ''}`} role="status">
      <img src={`/pictograms/${picto}.png`} alt="" className="picto" draggable={false} />
      <Title as="h2" size={compact ? 'section' : 'empty'}>{title}</Title>
      {body && <Body1 className="muted">{body}</Body1>}
      {children && <div className="row center">{children}</div>}
    </div>
  );
}

export function Section({ title, icon, action, children, className = '' }) {
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

export function Facts({ items }) {
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

const STATUS = {
  accepted: ['success', 'Accepted', CheckmarkCircle16Filled],
  pass: ['success', 'Passed', CheckmarkCircle16Filled],
  confirmed: ['success', 'Confirmed', CheckmarkCircle16Filled],
  done: ['success', 'Complete', CheckmarkCircle16Filled],
  current: ['success', 'Up to date', CheckmarkCircle16Filled],
  exported: ['success', 'Exported', CheckmarkCircle16Filled],
  connected: ['success', 'Connected', CheckmarkCircle16Filled],
  review: ['brand', 'Needs review', Eye16Regular],
  failed: ['danger', 'Failed', ErrorCircle16Filled],
  offline: ['danger', 'Offline', PlugDisconnected16Regular],
  running: ['brand', 'Processing', ArrowSync16Regular],
  queued: ['informative', 'Queued', Clock16Regular],
};
export function StatusBadge({ status }) {
  const [color, label, Icon] = STATUS[status] || ['informative', status, null];
  return <Badge appearance="tint" color={color} shape="rounded" icon={Icon ? <Icon /> : undefined}>{label}</Badge>;
}

export const pct = (n) => (n == null ? '' : `${Math.round(n * 100)}%`);
export const num = (s) => Number(String(s).replace(/[^0-9.-]/g, '')) || 0;
export const money = (n) => n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

// ISO 6346 container check digit.
export function iso6346(code) {
  const m = /^([A-Z]{4})(\d{6})(\d)$/.exec(code || '');
  if (!m) return false;
  const v = (c) => { const n = c.charCodeAt(0) - 55; return n + Math.floor((n - 1) / 10); }; // A=10, skip multiples of 11
  const sum = [...m[1]].map(v).concat([...m[2]].map(Number)).reduce((a, x, i) => a + x * 2 ** i, 0);
  return (sum % 11) % 10 === Number(m[3]);
}

// Checks run on the values as currently shown, so edits re-check live.
export function docChecks(doc, values) {
  const get = (k) => values?.[k] ?? fieldValue(doc, k);
  const out = [];
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
