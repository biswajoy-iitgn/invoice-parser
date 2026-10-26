// Workspace data. Documents come from samples.json (redacted pages from `sample invoices/` and photos made from them,
// built by scripts/build_samples.py). Everything else is configuration and history on one seeded timeline.
import SAMPLES_JSON from './samples.json';
import { count } from './format';
import type {
  AuditEntry, Connection, DocKind, DocStatus, FieldType, Note, Profile, ProfileField, Run, RunDoc, SampleDoc, Tables, Update, User, Values, Workflow,
} from './types';

const SAMPLES = SAMPLES_JSON as unknown as SampleDoc[];
type FieldSpec = [key: string, label: string, type: FieldType, group: string, hint?: string, columns?: string[]];

// Starting points for a profile. Any document can be read: pick the nearest template, or none, then add, rename or describe fields.
export const TEMPLATES: Record<string, { name: string; fields: FieldSpec[] }> = {
  invoice: { name: 'Invoice', fields: [
    ['invoice_no', 'Invoice number', 'Text', 'Invoice details'], ['invoice_date', 'Invoice date', 'Date', 'Invoice details'],
    ['vendor', 'Vendor', 'Text', 'Invoice details'], ['vendor_gstin', 'Vendor GSTIN', 'Text', 'Invoice details'],
    ['container', 'Container number', 'Text', 'Shipment'], ['hbl', 'House bill of lading', 'Text', 'Shipment'],
    ['mbl', 'Master bill of lading', 'Text', 'Shipment'], ['weight', 'Weight', 'Number', 'Shipment'], ['ex_rate', 'Exchange rate', 'Number', 'Shipment'],
    ['sea_freight', 'Sea freight', 'Amount', 'Charges'], ['thc', 'Terminal handling', 'Amount', 'Charges'], ['bl_fee', 'Bill of lading fee', 'Amount', 'Charges'],
    ['seal', 'Seal', 'Amount', 'Charges'], ['ens', 'ENS / AMS', 'Amount', 'Charges'], ['transport', 'Transportation', 'Amount', 'Charges'],
    ['agency', 'Agency', 'Amount', 'Charges'], ['warehouse', 'Warehouse', 'Amount', 'Charges'], ['other', 'Other charges', 'Amount', 'Charges'],
    ['total', 'Net total', 'Amount', 'Totals'], ['cgst', 'CGST', 'Amount', 'Totals'], ['sgst', 'SGST', 'Amount', 'Totals'], ['gross_total', 'Gross total', 'Amount', 'Totals'],
  ] },
  po: { name: 'Purchase order', fields: [
    ['po_no', 'PO number', 'Text', 'Order'], ['po_date', 'PO date', 'Date', 'Order'], ['supplier', 'Supplier', 'Text', 'Order'], ['buyer', 'Buyer', 'Text', 'Order'],
    ['item_code', 'Item code', 'Text', 'Lines'], ['qty', 'Quantity', 'Number', 'Lines'], ['unit_price', 'Unit price', 'Amount', 'Lines'],
    ['delivery_date', 'Delivery date', 'Date', 'Order'], ['po_total', 'PO total', 'Amount', 'Totals'],
  ] },
  dn: { name: 'Delivery note', fields: [
    ['dn_no', 'Delivery note number', 'Text', 'Delivery'], ['dn_date', 'Delivery date', 'Date', 'Delivery'], ['consignee', 'Consignee', 'Text', 'Delivery'],
    ['po_ref', 'PO reference', 'Text', 'Delivery'], ['packages', 'Packages', 'Number', 'Goods'], ['gross_weight', 'Gross weight', 'Number', 'Goods'],
    ['vehicle', 'Vehicle number', 'Text', 'Delivery'],
  ] },
  contract: { name: 'Contract', fields: [
    ['title', 'Agreement title', 'Text', 'Agreement'], ['parties', 'Parties', 'Table', 'Agreement', 'Every company or person that signs', ['Name', 'Role', 'Address']],
    ['effective_date', 'Effective date', 'Date', 'Term'], ['end_date', 'End date', 'Date', 'Term'], ['renews', 'Renews automatically', 'Yes/No', 'Term'],
    ['notice', 'Notice period', 'Text', 'Term', 'For example, 90 days before the end date'], ['value', 'Contract value', 'Amount', 'Money'],
    ['payment_terms', 'Payment terms', 'Long text', 'Money'], ['governing_law', 'Governing law', 'Text', 'Legal'],
    ['liability_cap', 'Liability cap', 'Text', 'Legal', 'The clause that limits liability, in a sentence'], ['signatories', 'Signatories', 'Table', 'Signatures', '', ['Name', 'Title', 'Date']],
  ] },
  letter: { name: 'Letter', fields: [
    ['sender', 'From', 'Text', 'Letter'], ['sender_org', 'Organization', 'Text', 'Letter'], ['recipient', 'To', 'Text', 'Letter'],
    ['letter_date', 'Date', 'Date', 'Letter'], ['reference', 'Reference', 'Text', 'Letter'], ['subject', 'Subject', 'Text', 'Content'],
    ['summary', 'Summary', 'Long text', 'Content', 'Two or three sentences on what the letter says or asks'], ['action', 'Needs a reply', 'Yes/No', 'Content'],
    ['reply_by', 'Reply by', 'Date', 'Content'],
  ] },
  datasheet: { name: 'Datasheet', fields: [
    ['product', 'Product name', 'Text', 'Product'], ['part_no', 'Part number', 'Text', 'Product'], ['manufacturer', 'Manufacturer', 'Text', 'Product'],
    ['revision', 'Revision', 'Text', 'Product'], ['issued', 'Issue date', 'Date', 'Product'],
    ['specs', 'Specifications', 'Table', 'Specifications', 'Every row of the specification tables', ['Parameter', 'Value', 'Unit', 'Condition']],
    ['op_temp', 'Operating temperature', 'Text', 'Specifications'], ['compliance', 'Compliance', 'Text', 'Specifications', 'For example, RoHS, REACH, ISO 9001'],
  ] },
  blank: { name: 'Something else', fields: [] },
};
export const CHARGE_KEYS: string[] = ['sea_freight', 'thc', 'bl_fee', 'seal', 'ens', 'transport', 'agency', 'warehouse', 'other'];

export const fieldsOf = (template: string, keys: string[] | 'all' = 'all', required: string[] = []): ProfileField[] =>
  (TEMPLATES[template]?.fields ?? []).map(([key, label, type, group, hint, columns]) => ({
    key, label, type, group, enabled: keys === 'all' || keys.includes(key), required: required.includes(key), ...(hint && { hint }), ...(columns && { columns }),
  }));
/** Field labels across every template, for screens that show a key without its profile. */
export const FIELD_LABELS: Record<string, string> = Object.fromEntries(Object.values(TEMPLATES).flatMap((t) => t.fields.map(([k, l]) => [k, l])));

export const PROFILES: Profile[] = [
  {
    id: 'freight', name: 'Freight invoice', docType: 'Invoice', template: 'invoice', preset: 'accurate', evaluator: true, onPremOnly: false,
    linked: ['fin.invoices', 'fin.invoice_charges'], languages: ['English'], exports: ['excel', 'database'], owner: 'Finance',
    fields: fieldsOf('invoice', 'all', ['invoice_no', 'invoice_date', 'vendor', 'gross_total']),
  },
  {
    id: 'po', name: 'Purchase order', docType: 'Purchase order', template: 'po', preset: 'balanced', evaluator: true, onPremOnly: true,
    linked: [], languages: ['English', 'Gujarati'], exports: ['csv'], owner: 'Procurement',
    fields: fieldsOf('po', ['po_no', 'po_date', 'supplier', 'item_code', 'qty', 'unit_price', 'po_total'], ['po_no']),
  },
  {
    id: 'dn', name: 'Delivery note', docType: 'Delivery note', template: 'dn', preset: 'fast', evaluator: false, onPremOnly: false,
    linked: ['erp.ap_staging'], languages: ['English', 'Hindi'], exports: ['database'], owner: 'Stores',
    fields: fieldsOf('dn', ['dn_no', 'dn_date', 'consignee', 'po_ref', 'packages'], ['dn_no']),
  },
  {
    id: 'contract', name: 'Supplier contract', docType: 'Contract', template: 'contract', preset: 'accurate', evaluator: true, onPremOnly: true,
    linked: [], languages: ['English'], exports: ['excel'], owner: 'Legal',
    fields: fieldsOf('contract', 'all', ['title', 'parties', 'effective_date']),
  },
];

// ---- documents ----
export const FLAG_BELOW = 0.85;
const twinOf: Record<string, SampleDoc> = Object.fromEntries(SAMPLES.filter((d) => d.kind === 'vector').map((d) => [d.id, d]));
export const DOCS: Record<string, SampleDoc> = Object.fromEntries(SAMPLES.map((d) => {
  if (d.kind !== 'scan') return [d.id, d];
  // Phone photo: same printed values as its digital twin, lower confidence, no measured boxes.
  const twin = twinOf[d.twin as string];
  return [d.id, { ...d, fields: twin.fields.map((f) => ({ ...f, box: null, conf: Math.max(0.6, +(f.conf - 0.02).toFixed(2)) })) }];
}));
export const docStatus = (doc: SampleDoc): DocStatus => (doc.fields?.some((f) => f.conf < FLAG_BELOW) ? 'review' : 'accepted');
export const meanConf = (doc: SampleDoc): number => +(doc.fields.reduce((a, f) => a + f.conf, 0) / doc.fields.length).toFixed(2);
export const fieldValue = (doc: SampleDoc, key: string): string => doc.fields?.find((f) => f.key === key)?.value || '';

// Every screen reads one timeline: today is 24 Oct 2026. Insights, databases, audit and notifications all derive from these batches.
export const DAYS = ['15 Oct', '16 Oct', '17 Oct', '18 Oct', '19 Oct', '20 Oct', '21 Oct', '22 Oct', '23 Oct', '24 Oct'];
const entry = (id: string, i: number, extra: Partial<RunDoc> = {}): RunDoc => {
  const d = DOCS[id];
  return { id, name: d.name, pages: d.pages.length, kind: d.kind, profile: 'freight', route: 0.97, status: docStatus(d), score: meanConf(d),
    seconds: d.kind === 'vector' ? 28 + i * 3 : 64 + i * 4, edited: false, ...extra };
};
const digital = SAMPLES.filter((d) => d.kind === 'vector').map((d) => d.id);
const scans = SAMPLES.filter((d) => d.kind === 'scan').map((d) => d.id);

// ---- history: batches read before today, from one fixed seed. Originals were archived after export; values are kept. ----
const rnd = ((seed: number) => (): number => { seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; })(20261024);
const pick = <T,>(a: T[]): T => a[Math.floor(rnd() * a.length)];
const int = (lo: number, hi: number): number => lo + Math.floor(rnd() * (hi - lo + 1));
const digits = (n: number): string => Array.from({ length: n }, () => int(0, 9)).join('');
const money2 = (paise: number): string => (paise / 100).toFixed(2);
const dayOffset = (day: string, back: number): string => { const d = new Date(`${day} 2026`); d.setDate(d.getDate() - back); return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }); };
const VENDORS = SAMPLES.filter((d) => d.kind === 'vector').map((d) => ({
  name: d.vendor, file: d.name.replace(/\.pdf$/, ''), gstin: fieldValue(d, 'vendor_gstin'), inv: fieldValue(d, 'invoice_no'), gross: Number(fieldValue(d, 'gross_total')) || 60000,
}));
const SUPPLIERS = ['Vatva Steel Supplies', 'Sanand Packaging Co.', 'Kathwada Forgings', 'Naroda Lubricants'];
const FLAG_POOL: Record<string, string[]> = { freight: ['mbl', 'mbl', 'transport', 'hbl', 'invoice_date', 'other', 'ex_rate'], po: ['unit_price', 'qty'], dn: ['packages', 'po_ref'] };
const taken = new Set(VENDORS.map((v) => v.inv));
const unique = (make: () => string): string => { let x: string; do x = make(); while (taken.has(x)); taken.add(x); return x; };
const pos: string[] = [];

function freightValues(day: string): Values & { _file: string } {
  const v = pick(VENDORS);
  const netP = Math.round(v.gross / 1.18 * (0.35 + rnd() * 1.3) * 100);
  const cgstP = Math.round(netP * 0.09);
  const keys = ['thc', 'bl_fee', 'seal', 'ens', 'transport', 'agency', 'warehouse', 'other'].sort(() => rnd() - 0.5).slice(0, int(1, 4));
  const shares = keys.map(() => 0.04 + rnd() * 0.12);
  const charges: Values = {};
  let left = netP;
  keys.forEach((k, i) => { const p = Math.round(netP * shares[i]); charges[k] = money2(p); left -= p; });
  charges.sea_freight = money2(left); // the rest; charges always add up to the net total
  return {
    invoice_no: unique(() => v.inv.replace(/\d{3}$/, digits(3))), invoice_date: dayOffset(day, int(2, 9)), vendor: v.name, vendor_gstin: v.gstin,
    ...(rnd() < 0.5 && { weight: `${int(800, 14000)} kg` }), ...(rnd() < 0.3 && { ex_rate: (83 + rnd() * 15).toFixed(2) }),
    ...charges, total: money2(netP), cgst: money2(cgstP), sgst: money2(cgstP), gross_total: money2(netP + 2 * cgstP), _file: v.file,
  };
}
function poValues(day: string): Values {
  const qty = int(50, 2000); const price = int(1200, 48000);
  const po = unique(() => `PO-45${digits(5)}`); pos.push(po);
  return { po_no: po, po_date: dayOffset(day, int(0, 3)), supplier: pick(SUPPLIERS), item_code: `HE-${pick(['BRG', 'CAG', 'RLR'])}-${digits(4)}`, qty: String(qty), unit_price: money2(price), po_total: money2(qty * price) };
}
function dnValues(day: string): Values {
  return { dn_no: unique(() => `DN-${digits(6)}`), dn_date: dayOffset(day, int(0, 2)), consignee: 'Harsha Engineers, Changodar', po_ref: pos.length ? pick(pos) : unique(() => `PO-45${digits(5)}`), packages: String(int(1, 40)) };
}
// [run, day, started, uploader, source, freight, purchase orders, delivery notes]
const PLAN: [string, string, string, string, string, number, number, number][] = [
  ['R-1033', '15 Oct', '15 Oct, 10:14', 'Priya Shah', 'Email inbox', 7, 3, 2],
  ['R-1034', '16 Oct', '16 Oct, 09:40', 'Priya Shah', 'Folder: Week 42 invoices', 8, 4, 2],
  ['R-1035', '17 Oct', '17 Oct, 11:05', 'Meera Joshi', 'Stores scans', 1, 1, 3],
  ['R-1036', '19 Oct', '19 Oct, 10:02', 'Priya Shah', 'Email inbox', 7, 4, 2],
  ['R-1037', '20 Oct', '20 Oct, 14:30', 'Rahul Patel', 'Folder: Purchase orders', 3, 9, 3],
  ['R-1038', '21 Oct', '21 Oct, 09:55', 'Priya Shah', 'Email inbox', 8, 2, 2],
  ['R-1039', '22 Oct', '22 Oct, 15:20', 'Meera Joshi', 'Stores scans', 2, 3, 9],
  ['R-1040', '23 Oct', '23 Oct, 09:31', 'Priya Shah', 'Email inbox', 5, 3, 1],
];
const HISTORY_RUNS: Run[] = PLAN.map(([id, day, started, by, source, nf, np, nd]) => {
  const types: string[] = [...Array(nf).fill('freight'), ...Array(np).fill('po'), ...Array(nd).fill('dn')];
  const docs = types.map((profile, i): RunDoc => {
    const values: Values = profile === 'freight' ? freightValues(day) : profile === 'po' ? poValues(day) : dnValues(day);
    const scan = profile === 'dn' ? rnd() < 0.5 : rnd() < 0.08;
    const { _file, ...vals } = values;
    const name = profile === 'freight' ? `${_file} ${vals.invoice_no}.${scan ? 'jpg' : 'pdf'}` : `${vals.po_no || vals.dn_no}.${scan ? 'jpg' : 'pdf'}`;
    const flagged = rnd() < 0.35 ? [pick(FLAG_POOL[profile])] : [];
    return {
      id: `${id}-${i + 1}`, name, kind: (scan ? 'scan' : 'vector') as DocKind, pages: int(1, 3), profile, route: +(0.9 + rnd() * 0.09).toFixed(2),
      status: profile === 'freight' && DAYS.indexOf(day) <= DAYS.indexOf('22 Oct') ? 'exported' : 'accepted',
      score: +(0.86 + rnd() * 0.11).toFixed(2), seconds: scan ? int(48, 96) : int(18, 46), flagged, edited: flagged.length > 0 && rnd() < 0.5,
      values: vals, archived: true, day,
    };
  });
  return { id, profile: 'auto', source, started, by, status: 'done' as const, day, docs };
}).reverse();

export const RUNS: Run[] = [
  { id: 'R-1042', profile: 'freight', source: 'Folder: August freight', started: '24 Oct, 09:52', by: 'Priya Shah', status: 'review', day: '24 Oct', docs: digital.map((id, i) => entry(id, i, { day: '24 Oct' })) },
  { id: 'R-1041', profile: 'auto', source: 'Phone uploads', started: '23 Oct, 16:05', by: 'Rahul Patel', status: 'review', day: '23 Oct', docs: scans.map((id, i) => entry(id, i, { route: 0.91, day: '23 Oct' })) },
  ...HISTORY_RUNS,
];
const histDocs = HISTORY_RUNS.flatMap((r) => r.docs.map((d) => ({ ...d, run: r.id })));
const exportedFreight = histDocs.filter((d) => d.status === 'exported' && d.profile === 'freight');

// ---- databases ----
export const CONNECTIONS: Connection[] = [
  { id: 'fin', name: 'Finance DB', kind: 'PostgreSQL', host: 'pg-fin.harsha.local:5432', database: 'invoices', status: 'connected' },
  { id: 'erp', name: 'ERP staging', kind: 'SQL Server', host: 'erp-stg.harsha.local:1433', database: 'staging', status: 'connected' },
  { id: 'wh', name: 'Analytics warehouse', kind: 'PostgreSQL', host: 'wh.harsha.local:5432', database: 'analytics', status: 'offline' },
];

const CHARGE_NAMES: Record<string, string> = { sea_freight: 'Sea freight', thc: 'Terminal handling', bl_fee: 'Bill of lading fee', seal: 'Seal', ens: 'ENS / AMS', transport: 'Transportation', agency: 'Agency', warehouse: 'Warehouse', other: 'Other charges' };
const vendorId = (name?: string): string => String(VENDORS.findIndex((x) => x.name === name) + 1);
const supplierCode = (name?: string): string => `SUP-${String(41 + VENDORS.findIndex((x) => x.name === name)).padStart(4, '0')}`;
// Column keys: 'PK', or 'FK <table>' for a reference to that table's key; the schema diagram draws these.
export const TABLES: Tables = {
  fin: {
    vendors: {
      columns: [['id', 'serial', 'PK'], ['name', 'varchar(120)', ''], ['gstin', 'varchar(15)', ''], ['city', 'varchar(64)', '']],
      rows: VENDORS.map((v, i) => [String(i + 1), v.name, v.gstin, 'Ahmedabad']),
    },
    invoices: {
      columns: [['invoice_no', 'varchar(32)', 'PK'], ['vendor_id', 'integer', 'FK vendors'], ['invoice_date', 'date', ''], ['currency', 'char(3)', ''], ['gross_total', 'numeric(14,2)', ''], ['status', 'varchar(16)', '']],
      rows: exportedFreight.map(({ values: v = {} }) => [v.invoice_no, vendorId(v.vendor), v.invoice_date, 'INR', v.gross_total, 'exported']),
    },
    charge_types: {
      columns: [['id', 'serial', 'PK'], ['code', 'varchar(16)', ''], ['name', 'varchar(48)', '']],
      rows: Object.entries(CHARGE_NAMES).map(([code, name], i) => [String(i + 1), code, name]),
    },
    invoice_charges: {
      columns: [['id', 'serial', 'PK'], ['invoice_no', 'varchar(32)', 'FK invoices'], ['charge_type_id', 'integer', 'FK charge_types'], ['amount', 'numeric(14,2)', '']],
      rows: exportedFreight.flatMap(({ values: v = {} }) => Object.keys(CHARGE_NAMES).filter((k) => v[k]).map((k) => [v.invoice_no, String(Object.keys(CHARGE_NAMES).indexOf(k) + 1), v[k]]))
        .map((r, i) => [String(901 + i), ...r]),
    },
  },
  erp: {
    suppliers: {
      columns: [['supplier_code', 'nvarchar(16)', 'PK'], ['name', 'nvarchar(120)', ''], ['gstin', 'nvarchar(15)', '']],
      rows: VENDORS.map((v) => [supplierCode(v.name), v.name, v.gstin]),
    },
    ap_staging: {
      columns: [['doc_ref', 'nvarchar(32)', 'PK'], ['supplier_code', 'nvarchar(16)', 'FK suppliers'], ['amount', 'decimal(14,2)', ''], ['posted', 'bit', '']],
      rows: exportedFreight.map(({ values: v = {} }) => [v.invoice_no, supplierCode(v.vendor), v.gross_total, '1']),
    },
  },
  wh: {},
};

export const WORKFLOWS: Workflow[] = [
  { id: 'wf-1', name: 'Send flagged invoices for approval', when: 'review', action: 'approval', target: 'Finance approvers', profiles: ['freight'], enabled: true, last: '24 Oct, 09:58' },
  { id: 'wf-2', name: 'Post approved invoices to ERP', when: 'accepted', action: 'webhook', target: 'https://erp-stg.harsha.local/api/ap', profiles: ['freight'], body: '{\n  "document": "{{file_name}}",\n  "fields": {{fields_json}}\n}', enabled: true, last: '23 Oct, 18:21' },
  { id: 'wf-3', name: 'Batch summary to Teams', when: 'complete', action: 'notify', target: 'Teams: #accounts-payable', profiles: 'all', enabled: false, last: '22 Oct, 17:02' },
];
/** An automation applies to all profiles, new ones included, or to a list. */
export const appliesTo = (w: Workflow, profileId: string): boolean => w.profiles === 'all' || w.profiles.includes(profileId);
export const WHEN: Record<Workflow['when'], string> = { review: 'A document needs review', accepted: 'A document is approved', complete: 'A batch finishes' };
export const ACTION: Record<Workflow['action'], string> = { webhook: 'Call a webhook', notify: 'Send email or Teams message', approval: 'Ask for approval' };

// Plain-language stage names; engines keep their technical names on the admin page.
export const PATHS: Record<DocKind, string[]> = {
  vector: ['Detecting document type', 'Reading digital text', 'Finding page layout', 'Extracting fields with AI', 'Matching fields', 'Checking accuracy'],
  scan: ['Detecting document type', 'Enhancing image', 'Finding page layout', 'Recognizing text', 'Extracting fields with AI', 'Matching fields', 'Checking accuracy'],
};

export const USERS: User[] = [
  { id: 'u1', name: 'Biswajoy Bhattacharjee', email: 'biswajoy.bhattacharjee@harsha.com', role: 'admin', groups: ['Finance approvers'], last: '24 Oct, 10:04' },
  { id: 'u2', name: 'Priya Shah', email: 'priya.shah@harsha.com', role: 'user', groups: ['Finance approvers'], last: '24 Oct, 09:58' },
  { id: 'u3', name: 'Rahul Patel', email: 'rahul.patel@harsha.com', role: 'user', groups: [], last: '23 Oct, 18:20' },
  { id: 'u4', name: 'Meera Joshi', email: 'meera.joshi@harsha.com', role: 'user', groups: ['Stores'], last: '22 Oct, 16:40' },
];

const exportedOn22 = exportedFreight.filter((d) => d.day === '22 Oct').length;
const editedOn23 = (histDocs.find((d) => d.day === '23 Oct' && d.edited) || histDocs.find((d) => d.edited)) as RunDoc & { flagged: string[] };
// Hashes are filled in when the app starts (src/audit.ts), so the seeded log is a real chain.
export const AUDIT: AuditEntry[] = [
  { at: '24 Oct, 10:04:12', actor: 'Biswajoy Bhattacharjee', action: 'Tested engine connection', target: 'AI model: Local Qwen', hash: '' },
  { at: '24 Oct, 10:00:05', actor: 'System', action: 'Refreshed insights', target: 'After batch R-1042', hash: '' },
  { at: '24 Oct, 09:57:02', actor: 'System', action: 'Finished batch', target: 'R-1042, 7 documents', hash: '' },
  { at: '23 Oct, 18:20:11', actor: 'Rahul Patel', action: 'Exported to database', target: `Finance DB, invoices, ${count(exportedOn22, 'row', 'rows')}`, hash: '' },
  { at: '23 Oct, 17:02:55', actor: 'Biswajoy Bhattacharjee', action: 'Published profile', target: 'Freight invoice v7', hash: '' },
  { at: '23 Oct, 11:12:40', actor: 'Priya Shah', action: 'Edited field', target: `${editedOn23.name}, ${FIELD_LABELS[editedOn23.flagged[0]]}`, hash: '' },
  { at: '23 Oct, 02:00:03', actor: 'System', action: 'Refreshed insights', target: 'Nightly job', hash: '' },
];

export const UPDATES: Update[] = [
  { name: 'PP-OCRv5 Server', engine: 'Text recognition', current: '3.1.0', latest: '3.2.1', notes: 'Better accuracy on rotated phone photos' },
  { name: 'Qwen3-VL-8B Invoice LoRA', engine: 'AI model', current: 'v2.3', latest: 'v2.3', notes: '' },
  { name: 'PP-DocLayout-L', engine: 'Page layout', current: '1.4.0', latest: '1.4.0', notes: '' },
  { name: 'SwinIR Document x2', engine: 'Image enhancement', current: '0.9.2', latest: '1.0.0', notes: 'Faster on CPU' },
  { name: 'PyMuPDF', engine: 'Digital text', current: '1.26.4', latest: '1.26.5', notes: 'Security fix' },
  { name: 'LAYA cross-encoder', engine: 'Field matching', current: 'v4', latest: 'v4', notes: '' },
];

export const NOTIFICATIONS: Note[] = [
  { id: 'n3', kind: 'insight', title: 'Possible duplicate invoice found', body: 'DACHSER.pdf and DACHSER_phone.jpg', time: '10:00', page: ['insights'] },
  { id: 'n1', kind: 'approval', title: 'DACHSER.pdf is waiting for your approval', time: '09:58', page: ['review', 'doc-dachser'] },
  { id: 'n2', kind: 'run', title: 'Batch R-1042 finished', body: '7 documents, 5 need review', time: '09:57', page: ['run', 'R-1042'] },
  { id: 'n4', kind: 'engine', title: 'Accuracy check API is offline', body: 'Falling back to the local model', time: '01:12', page: ['engines'], admin: true },
  { id: 'n5', kind: 'engine', title: 'Claude API used 82% of its monthly budget', time: 'Yesterday', page: ['engines'], admin: true },
];

export const HISTORY = {
  updated: '24 Oct, 10:00',
  engines: [
    { name: 'Document type detection', latency: '60 ms', failures: '0.1%' }, { name: 'Digital text', latency: '90 ms', failures: '0%' },
    { name: 'Image enhancement', latency: '1.1 s', failures: '0.4%' }, { name: 'Page layout', latency: '140 ms', failures: '0.2%' },
    { name: 'Text recognition', latency: '310 ms', failures: '1.1%' }, { name: 'AI model', latency: '2.4 s', failures: '0.6%' },
    { name: 'Field matching', latency: '35 ms', failures: '0%' }, { name: 'Accuracy check', latency: '3.8 s', failures: '2.3%' },
  ],
  budgets: [{ name: 'Claude API, accuracy check', spent: 410, cap: 500 }, { name: 'Azure Vision Read, OCR fallback', spent: 38, cap: 200 }],
};
