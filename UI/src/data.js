// Demo data. Documents come from samples.json (real pages from `sample invoices/` and phone photos,
// built by scripts/build_samples.py). Everything else is mock configuration and history.
import SAMPLES from './samples.json';

export const FIELD_LIBRARY = {
  invoice: [
    ['invoice_no', 'Invoice number', 'Text', 'Invoice details'], ['invoice_date', 'Invoice date', 'Date', 'Invoice details'],
    ['vendor', 'Vendor', 'Text', 'Invoice details'], ['vendor_gstin', 'Vendor GSTIN', 'Text', 'Invoice details'],
    ['container', 'Container number', 'Text', 'Shipment'], ['hbl', 'House bill of lading', 'Text', 'Shipment'],
    ['mbl', 'Master bill of lading', 'Text', 'Shipment'], ['weight', 'Weight', 'Number', 'Shipment'], ['ex_rate', 'Exchange rate', 'Number', 'Shipment'],
    ['sea_freight', 'Sea freight', 'Amount', 'Charges'], ['thc', 'Terminal handling', 'Amount', 'Charges'], ['bl_fee', 'Bill of lading fee', 'Amount', 'Charges'],
    ['seal', 'Seal', 'Amount', 'Charges'], ['ens', 'ENS / AMS', 'Amount', 'Charges'], ['transport', 'Transportation', 'Amount', 'Charges'],
    ['agency', 'Agency', 'Amount', 'Charges'], ['warehouse', 'Warehouse', 'Amount', 'Charges'], ['other', 'Other charges', 'Amount', 'Charges'],
    ['total', 'Net total', 'Amount', 'Totals'], ['cgst', 'CGST', 'Amount', 'Totals'], ['sgst', 'SGST', 'Amount', 'Totals'], ['gross_total', 'Gross total', 'Amount', 'Totals'],
  ],
  po: [
    ['po_no', 'PO number', 'Text', 'Order'], ['po_date', 'PO date', 'Date', 'Order'], ['supplier', 'Supplier', 'Text', 'Order'], ['buyer', 'Buyer', 'Text', 'Order'],
    ['item_code', 'Item code', 'Text', 'Lines'], ['qty', 'Quantity', 'Number', 'Lines'], ['unit_price', 'Unit price', 'Amount', 'Lines'],
    ['delivery_date', 'Delivery date', 'Date', 'Order'], ['po_total', 'PO total', 'Amount', 'Totals'],
  ],
  dn: [
    ['dn_no', 'Delivery note number', 'Text', 'Delivery'], ['dn_date', 'Delivery date', 'Date', 'Delivery'], ['consignee', 'Consignee', 'Text', 'Delivery'],
    ['po_ref', 'PO reference', 'Text', 'Delivery'], ['packages', 'Packages', 'Number', 'Goods'], ['gross_weight', 'Gross weight', 'Number', 'Goods'],
    ['vehicle', 'Vehicle number', 'Text', 'Delivery'],
  ],
};
export const CHARGE_KEYS = ['sea_freight', 'thc', 'bl_fee', 'seal', 'ens', 'transport', 'agency', 'warehouse', 'other'];

const fieldsOf = (lib, keys, required = []) =>
  FIELD_LIBRARY[lib].map(([key, label, type, group]) => ({ key, label, type, group, enabled: keys === 'all' || keys.includes(key), required: required.includes(key) }));

export const PROFILES = [
  {
    id: 'freight', name: 'Freight invoice', lib: 'invoice', icon: 'receipt', preset: 'accurate', aiSource: 'local', evaluator: true, onPremOnly: false,
    linked: ['fin.invoices', 'fin.invoice_charges'], languages: ['English'], exports: ['excel', 'database'], owner: 'Finance',
    fields: fieldsOf('invoice', 'all', ['invoice_no', 'invoice_date', 'vendor', 'gross_total']),
  },
  {
    id: 'po', name: 'Purchase order', lib: 'po', icon: 'clipboard', preset: 'balanced', aiSource: 'local', evaluator: true, onPremOnly: true,
    linked: [], languages: ['English', 'Gujarati'], exports: ['csv'], owner: 'Procurement',
    fields: fieldsOf('po', ['po_no', 'po_date', 'supplier', 'item_code', 'qty', 'unit_price', 'po_total'], ['po_no']),
  },
  {
    id: 'dn', name: 'Delivery note', lib: 'dn', icon: 'box', preset: 'fast', aiSource: 'cloud', evaluator: false, onPremOnly: false,
    linked: ['erp.ap_staging'], languages: ['English', 'Hindi'], exports: ['database'], owner: 'Stores',
    fields: fieldsOf('dn', ['dn_no', 'dn_date', 'consignee', 'po_ref', 'packages'], ['dn_no']),
  },
];

// ---- documents ----
export const FLAG_BELOW = 0.85;
const twinOf = Object.fromEntries(SAMPLES.filter((d) => d.kind === 'vector').map((d) => [d.id, d]));
export const DOCS = Object.fromEntries(SAMPLES.map((d) => {
  if (d.kind !== 'scan') return [d.id, d];
  // Phone photo: same printed values as its digital twin, lower confidence, no measured boxes.
  const twin = twinOf[d.twin];
  return [d.id, { ...d, fields: twin.fields.map((f) => ({ ...f, box: null, conf: Math.max(0.6, +(f.conf - 0.02).toFixed(2)) })) }];
}));
export const docStatus = (doc) => (doc.fields?.some((f) => f.conf < FLAG_BELOW) ? 'review' : 'accepted');
export const meanConf = (doc) => +(doc.fields.reduce((a, f) => a + f.conf, 0) / doc.fields.length).toFixed(2);
export const fieldValue = (doc, key) => doc.fields?.find((f) => f.key === key)?.value || '';

const entry = (id, extra = {}) => {
  const d = DOCS[id];
  return { id, name: d.name, pages: d.pages.length, kind: d.kind, profile: 'freight', route: 0.97, status: docStatus(d), score: meanConf(d), ...extra };
};
const digital = SAMPLES.filter((d) => d.kind === 'vector').map((d) => d.id);
const scans = SAMPLES.filter((d) => d.kind === 'scan').map((d) => d.id);

export const RUNS = [
  { id: 'R-1042', profile: 'freight', source: 'Folder: August freight', started: '21 Aug, 10:22', by: 'Priya Shah', status: 'review', docs: digital.map((id) => entry(id)) },
  { id: 'R-1041', profile: 'auto', source: 'Phone uploads', started: '20 Aug, 16:05', by: 'Rahul Patel', status: 'review', docs: scans.map((id) => entry(id, { route: 0.91 })) },
];

// ---- databases ----
export const CONNECTIONS = [
  { id: 'fin', name: 'Finance DB', kind: 'PostgreSQL', host: 'pg-fin.harsha.local:5432', database: 'invoices', status: 'connected' },
  { id: 'erp', name: 'ERP staging', kind: 'SQL Server', host: 'erp-stg.harsha.local:1433', database: 'staging', status: 'connected' },
  { id: 'wh', name: 'Analytics warehouse', kind: 'PostgreSQL', host: 'wh.harsha.local:5432', database: 'analytics', status: 'offline' },
];

const invRow = (id, status) => {
  const d = DOCS[id];
  return [fieldValue(d, 'invoice_no'), d.vendor, fieldValue(d, 'invoice_date'), 'INR', fieldValue(d, 'gross_total'), status];
};
export const TABLES = {
  fin: {
    invoices: {
      columns: [['invoice_no', 'varchar(32)', 'PK'], ['vendor', 'varchar(120)', ''], ['invoice_date', 'date', ''], ['currency', 'char(3)', ''], ['gross_total', 'numeric(14,2)', ''], ['status', 'varchar(16)', '']],
      rows: [invRow('doc-jeena', 'exported'), invRow('doc-megalo', 'exported'), invRow('doc-ept-exim', 'exported')],
    },
    invoice_charges: {
      columns: [['id', 'serial', 'PK'], ['invoice_no', 'varchar(32)', 'FK invoices'], ['charge_type', 'varchar(48)', 'FK charge_types'], ['amount', 'numeric(14,2)', '']],
      rows: [
        ['901', 'IVJCAMD270007254', 'Agency', '3450.00'], ['902', 'IVJCAMD270007254', 'Warehouse', '26900.00'], ['903', 'IVJCAMD270007254', 'Other charges', '2500.00'],
        ['904', 'AMDTI26-27-0289', 'Bill of lading fee', '5500.00'], ['905', 'ERIA261452', 'Agency', '1800.00'], ['906', 'ERIA261452', 'Transportation', '6126.00'],
      ],
    },
    vendors: {
      columns: [['id', 'serial', 'PK'], ['name', 'varchar(120)', ''], ['gstin', 'varchar(15)', ''], ['city', 'varchar(64)', '']],
      rows: SAMPLES.filter((d) => d.kind === 'vector').map((d, i) => [String(i + 1), d.vendor, fieldValue(d, 'vendor_gstin'), 'Ahmedabad']),
    },
  },
  erp: {
    ap_staging: {
      columns: [['doc_ref', 'nvarchar(32)', 'PK'], ['supplier_code', 'nvarchar(16)', ''], ['amount', 'decimal(14,2)', ''], ['posted', 'bit', '']],
      rows: [['IVJCAMD270007254', 'SUP-0091', '39058.00', '1'], ['AMDTI26-27-0289', 'SUP-0044', '958657.00', '0']],
    },
  },
  wh: {},
};

export const WORKFLOWS = [
  { id: 'wf-1', name: 'Send flagged invoices for approval', when: 'review', action: 'approval', target: 'Finance approvers', profile: 'freight', enabled: true, last: '21 Aug, 10:31' },
  { id: 'wf-2', name: 'Post accepted invoices to ERP', when: 'accepted', action: 'webhook', target: 'https://erp-stg.harsha.local/api/ap', profile: 'freight', enabled: true, last: '21 Aug, 10:30' },
  { id: 'wf-3', name: 'Batch summary to Teams', when: 'complete', action: 'notify', target: 'Teams: #accounts-payable', profile: 'all', enabled: false, last: '18 Aug, 17:02' },
];
export const WHEN = { review: 'A document needs review', accepted: 'A document is accepted', complete: 'A batch finishes' };
export const ACTION = { webhook: 'Call a webhook', notify: 'Send email or Teams message', approval: 'Ask for approval' };

// Plain-language stage names; engines keep their technical names on the admin page.
export const PATHS = {
  vector: ['Detecting document type', 'Reading digital text', 'Finding page layout', 'Extracting fields with AI', 'Matching fields', 'Checking accuracy'],
  scan: ['Detecting document type', 'Enhancing image', 'Finding page layout', 'Recognizing text', 'Extracting fields with AI', 'Matching fields', 'Checking accuracy'],
};

export const USERS = [
  { id: 'u1', name: 'Biswajoy Bhattacharjee', email: 'biswajoy.b@iitgn.ac.in', role: 'admin', groups: ['Finance approvers'], last: '24 Oct, 10:02' },
  { id: 'u2', name: 'Priya Shah', email: 'priya.shah@harsha.com', role: 'user', groups: ['Finance approvers'], last: '24 Oct, 09:41' },
  { id: 'u3', name: 'Rahul Patel', email: 'rahul.patel@harsha.com', role: 'user', groups: [], last: '23 Oct, 18:15' },
  { id: 'u4', name: 'Meera Joshi', email: 'meera.joshi@harsha.com', role: 'user', groups: ['Stores'], last: '22 Oct, 11:30' },
];

export const AUDIT = [
  { at: '24 Oct, 10:04:12', actor: 'Biswajoy Bhattacharjee', action: 'Tested engine connection', target: 'AI model: Local Qwen', hash: '9f2c4e1a' },
  { at: '24 Oct, 09:58:40', actor: 'Priya Shah', action: 'Edited field', target: 'DACHSER.pdf, Master bill of lading', hash: '71bd09c3' },
  { at: '24 Oct, 09:57:02', actor: 'System', action: 'Finished batch', target: 'R-1042, 7 documents', hash: 'c08e55f7' },
  { at: '23 Oct, 18:20:11', actor: 'Rahul Patel', action: 'Exported to database', target: 'Finance DB, invoices, 3 rows', hash: '3a6d2b90' },
  { at: '23 Oct, 17:02:55', actor: 'Biswajoy Bhattacharjee', action: 'Published profile', target: 'Freight invoice v7', hash: 'e41f7c28' },
  { at: '23 Oct, 02:00:03', actor: 'System', action: 'Refreshed insights', target: 'Nightly job', hash: '5c93a0de' },
];

export const UPDATES = [
  { name: 'PP-OCRv5 Server', engine: 'Text recognition', current: '3.1.0', latest: '3.2.1', notes: 'Better accuracy on rotated phone photos' },
  { name: 'Qwen3-VL-8B Invoice LoRA', engine: 'AI model', current: 'v2.3', latest: 'v2.3', notes: '' },
  { name: 'PP-DocLayout-L', engine: 'Page layout', current: '1.4.0', latest: '1.4.0', notes: '' },
  { name: 'SwinIR Document x2', engine: 'Image enhancement', current: '0.9.2', latest: '1.0.0', notes: 'Faster on CPU' },
  { name: 'PyMuPDF', engine: 'Digital text', current: '1.26.4', latest: '1.26.5', notes: 'Security fix' },
  { name: 'LAYA cross-encoder', engine: 'Field matching', current: 'v4', latest: 'v4', notes: '' },
];

export const NOTIFICATIONS = [
  { id: 'n1', kind: 'approval', title: 'DACHSER.pdf is waiting for your approval', time: '10:31', page: ['review', 'doc-dachser'] },
  { id: 'n2', kind: 'run', title: 'Batch R-1042 finished', body: '7 documents, 5 need review', time: '10:30', page: ['run', 'R-1042'] },
  { id: 'n3', kind: 'insight', title: 'Possible duplicate invoice found', body: 'DACHSER.pdf and DACHSER_phone.jpg', time: '02:00', page: ['insights'] },
  { id: 'n4', kind: 'engine', title: 'Accuracy check API is offline', body: 'Falling back to the local model', time: '01:12', page: ['engines'], admin: true },
  { id: 'n5', kind: 'engine', title: 'Claude API used 82% of its monthly budget', time: 'Yesterday', page: ['engines'], admin: true },
];

const days = ['15 Oct', '16 Oct', '17 Oct', '18 Oct', '19 Oct', '20 Oct', '21 Oct', '22 Oct', '23 Oct', '24 Oct'];
export const HISTORY = {
  updated: '24 Oct, 02:00',
  days,
  volume: [
    { name: 'Freight invoice', data: [42, 51, 38, 0, 0, 47, 55, 49, 61, 12] },
    { name: 'Purchase order', data: [18, 22, 25, 0, 0, 20, 24, 19, 27, 4] },
    { name: 'Delivery note', data: [9, 11, 7, 3, 0, 10, 12, 9, 14, 2] },
  ],
  autoAccept: [88, 90, 89, 91, 92, 93, 92, 94, 93].map((y, i) => ({ x: ['15 Oct', '16 Oct', '17 Oct', '18 Oct', '20 Oct', '21 Oct', '22 Oct', '23 Oct', '24 Oct'][i], y })),
  medianSeconds: 41,
  corrected: [['Master bill of lading', 23], ['Transportation', 17], ['Exchange rate', 12], ['House bill of lading', 9], ['Invoice date', 6]],
  engines: [
    { name: 'Document type detection', latency: '60 ms', failures: '0.1%' }, { name: 'Digital text', latency: '90 ms', failures: '0%' },
    { name: 'Image enhancement', latency: '1.1 s', failures: '0.4%' }, { name: 'Page layout', latency: '140 ms', failures: '0.2%' },
    { name: 'Text recognition', latency: '310 ms', failures: '1.1%' }, { name: 'AI model', latency: '2.4 s', failures: '0.6%' },
    { name: 'Field matching', latency: '35 ms', failures: '0%' }, { name: 'Accuracy check', latency: '3.8 s', failures: '2.3%' },
  ],
  budgets: [{ name: 'Claude API, accuracy check', spent: 410, cap: 500 }, { name: 'Azure Vision Read, OCR fallback', spent: 38, cap: 200 }],
};
