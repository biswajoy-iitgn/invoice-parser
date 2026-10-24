import { useState } from 'react';
import {
  Button, TabList, Tab, Field, RadioGroup, Radio, Dropdown, Option, Input, SpinButton, Slider, Switch, Checkbox, Textarea,
  Accordion, AccordionItem, AccordionHeader, AccordionPanel, Card, CardHeader, Caption1, Spinner, Text, ProgressBar,
  Table, TableHeader, TableRow, TableHeaderCell, TableBody, TableCell, Tooltip,
  Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
} from '@fluentui/react-components';
import {
  SaveRegular, PlugConnectedRegular, AddRegular, ArrowUpRegular, ArrowDownRegular, EditRegular, DeleteRegular, ChevronRightRegular,
} from '@fluentui/react-icons';
import { useApp, PageHeader, StatusBadge, Picto, Title } from '../ui';
import { Crumbs } from './Runs';

const LLM_PROVIDERS = ['Anthropic Claude', 'Azure OpenAI', 'OpenAI-Compatible', 'Google Vertex AI', 'AWS Bedrock', 'Custom REST'];
const ENGINES = [
  { id: 'router', name: 'Document type detection', tech: 'Router, picks the profile', picto: "magnifying_glass_tilted_right", models: ['DocType Classifier', 'Rules Only'], runtimes: ['ONNX Runtime'], providers: ['Azure Document Intelligence Classifier', 'Custom REST'] },
  { id: 'vector', name: 'Digital text', tech: 'Vector text extraction for digital PDFs', picto: "page_facing_up", models: ['PyMuPDF Text Layer', 'pdfplumber'], runtimes: ['Python'], providers: ['Custom REST'] },
  { id: 'upscaler', name: 'Image enhancement', tech: 'Resolution upscaler for scans and photos', picto: "framed_picture", models: ['SwinIR Document x2', 'SwinIR Document x4', 'Bicubic'], runtimes: ['PyTorch', 'ONNX Runtime'], providers: ['Custom REST'] },
  { id: 'layout', name: 'Page layout', tech: 'Document layout analyser', picto: "triangular_ruler", models: ['PP-DocLayout-L', 'MinerU 2.5', 'Docling Heron'], runtimes: ['ONNX Runtime', 'PaddlePaddle'], providers: ['Azure Document Intelligence', 'Google Document AI', 'AWS Textract', 'Custom REST'] },
  { id: 'ocr', name: 'Text recognition', tech: 'OCR engine', picto: "memo", models: ['PP-OCRv5 Server', 'PP-OCRv5 Mobile', 'Tesseract 5'], runtimes: ['PaddlePaddle', 'ONNX Runtime'], providers: ['Azure AI Vision Read', 'Google Cloud Vision', 'AWS Textract', 'Custom REST'] },
  { id: 'llm', name: 'AI model', tech: 'LLM, local or API', picto: "sparkles", models: ['Qwen3-VL-8B Invoice LoRA', 'Qwen3-VL-8B Instruct'], runtimes: ['llama.cpp', 'Ollama', 'vLLM', 'Transformers'], providers: LLM_PROVIDERS },
  { id: 'decision', name: 'Field matching', tech: 'Decision model and validation rules', picto: "puzzle_piece", models: ['LAYA Cross-Encoder + bge-small', 'Rules Only'], runtimes: ['ONNX Runtime', 'PyTorch'], providers: ['Azure ML Endpoint', 'AWS SageMaker Endpoint', 'Custom REST'] },
  { id: 'evaluator', name: 'Accuracy check', tech: 'LLM evaluator', picto: "shield", models: ['Qwen3-8B Judge'], runtimes: ['llama.cpp', 'Ollama', 'vLLM'], providers: LLM_PROVIDERS },
];
const AUTH = { apikey: 'API key', bearer: 'Bearer token', oauth: 'OAuth client credentials', iam: 'Cloud IAM role', none: 'None' };

const local = (e, port, extra = {}) => ({ id: `${e.id}-local`, name: `Local ${e.models[0].split(' ')[0]}`, mode: 'local', model: e.models[0], runtime: e.runtimes[0], host: `http://127.0.0.1:${port}`, workers: 2, status: 'connected', latency: extra.latency || '', ...extra });
const api = (o) => ({ mode: 'api', auth: 'apikey', key: '', modelId: '', timeout: 60, retries: 2, rate: 120, budget: 0, spent: 0, headers: '', status: 'offline', latency: '', ...o });

const E = Object.fromEntries(ENGINES.map((e) => [e.id, e]));
const INITIAL_CONNS = {
  router: [local(E.router, 8094, { latency: '60 ms' })],
  vector: [local(E.vector, 8095, { latency: '90 ms' })],
  upscaler: [local(E.upscaler, 8096, { latency: '1.1 s' })],
  layout: [local(E.layout, 8091, { latency: '140 ms' })],
  ocr: [local(E.ocr, 8092, { latency: '310 ms' }), api({ id: 'ocr-az', name: 'Azure Vision Read', provider: 'Azure AI Vision Read', endpoint: 'https://harsha-vision.cognitiveservices.azure.com/', auth: 'oauth', budget: 200, spent: 38, status: 'connected', latency: '640 ms' })],
  llm: [local(E.llm, 8080, { latency: '2.4 s' }), api({ id: 'llm-claude', name: 'Claude API', provider: 'Anthropic Claude', endpoint: 'https://api.anthropic.com/v1/messages', modelId: 'claude-sonnet-5-5', budget: 500, spent: 410, status: 'connected', latency: '1.9 s' })],
  decision: [local(E.decision, 8093, { latency: '35 ms' })],
  evaluator: [api({ id: 'eval-claude', name: 'Claude API', provider: 'Anthropic Claude', endpoint: 'https://api.anthropic.com/v1/messages', modelId: 'claude-sonnet-5-5', budget: 500, spent: 410 }), local(E.evaluator, 8081, { latency: '3.8 s' })],
};

const SETTINGS = {
  minDocConf: 0.7, unknown: 'review', rules: [['TAX INVOICE + DACHSER', 'Freight Invoice'], ['PURCHASE ORDER', 'Purchase Order'], ['DELIVERY CHALLAN', 'Delivery Note']],
  sources: [['Scanner: Stores Gate 2', 'Delivery Note'], ['Folder: Aug Freight', 'Freight Invoice']],
  minCoverage: 85, mixedPages: true, tolerance: 2,
  upMode: 'auto', belowDpi: 150, maxScale: 'x4',
  threshold: 0.5, tables: true, order: true, maxPages: 50,
  dpi: 200, trust: 0.95, langs: ['English', 'Hindi', 'Gujarati'],
  temperature: 0, maxTokens: 2048, json: true, prompt: 'Return only JSON matching the profile schema. Cite every value to a text span. Leave unprinted values blank.',
  accept: 0.9, autoAccept: true, checks: ['Charges add up', 'Tax sum', 'Container number (ISO 6346)', 'MAWB check digit', 'Date range'],
  sample: 100, criteria: ['Value is on the document', 'Format is valid', 'Fields agree with each other'], onFail: 'review', pass: 0.85,
  redact: ['GSTIN', 'PAN', 'Bank account', 'Phone', 'Email'], redactMode: 'mask', logPayloads: false, retention: 30,
};

function SliderField({ label, value, min, max, step, onChange, fmt = (v) => v }) {
  return (
    <Field label={`${label}: ${fmt(value)}`}>
      <Slider min={min} max={max} step={step} value={value} aria-valuetext={String(fmt(value))} onChange={(_, d) => onChange(d.value)} />
    </Field>
  );
}

function Checks({ label, options, value, onChange }) {
  return (
    <Field label={label}>
      {options.map((o) => <Checkbox key={o} label={o} checked={value.includes(o)} onChange={(_, d) => onChange(d.checked ? [...value, o] : value.filter((x) => x !== o))} />)}
    </Field>
  );
}

function PairTable({ label, cols, rows, onChange, options }) {
  return (
    <div className="stack span-2">
      <Text weight="semibold">{label}</Text>
      <div className="table-wrap">
        <Table size="small" aria-label={label} className="responsive auto-table">
          <TableHeader><TableRow><TableHeaderCell>{cols[0]}</TableHeaderCell><TableHeaderCell>{cols[1]}</TableHeaderCell><TableHeaderCell><span className="sr-only">Remove</span></TableHeaderCell></TableRow></TableHeader>
          <TableBody>
            {rows.map(([a, b], i) => (
              <TableRow key={i}>
                <TableCell data-label={cols[0]}><Input size="small" aria-label={cols[0]} value={a} onChange={(_, d) => onChange(rows.map((r, k) => (k === i ? [d.value, r[1]] : r)))} /></TableCell>
                <TableCell data-label={cols[1]}>
                  <Dropdown size="small" aria-label={cols[1]} value={b} selectedOptions={[b]} onOptionSelect={(_, d) => onChange(rows.map((r, k) => (k === i ? [r[0], d.optionValue] : r)))}>
                    {options.map((o) => <Option key={o}>{o}</Option>)}
                  </Dropdown>
                </TableCell>
                <TableCell><Tooltip content="Remove" relationship="label"><Button appearance="subtle" size="small" icon={<DeleteRegular />} onClick={() => onChange(rows.filter((_, k) => k !== i))} /></Tooltip></TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <div><Button size="small" icon={<AddRegular />} onClick={() => onChange([...rows, ['', options[0]]])}>Add</Button></div>
    </div>
  );
}

function ConnectionDialog({ engine, conn, onClose, onSave }) {
  const [c, setC] = useState(conn);
  const set = (patch) => setC({ ...c, ...patch });
  const num = (k) => (_, d) => set({ [k]: d.value ?? Number(d.displayValue) });
  const badUrl = c.mode === 'api' && c.endpoint && !c.endpoint.startsWith('https://');
  const ok = c.name.trim() && (c.mode === 'local' ? c.host : c.endpoint && !badUrl);
  return (
    <Dialog open onOpenChange={(_, d) => !d.open && onClose()}>
      <DialogSurface className="wide-dialog">
        <DialogBody>
          <DialogTitle>{`${conn.id ? 'Edit' : 'Add'} a connection for ${engine.name}`}</DialogTitle>
          <DialogContent className="form">
            <Field label="Name" required><Input value={c.name} onChange={(_, d) => set({ name: d.value })} /></Field>
            <Field label="Source">
              <RadioGroup layout="horizontal" value={c.mode} onChange={(_, d) => set(d.value === 'api' ? api({ ...c, mode: 'api', provider: c.provider || engine.providers[0], endpoint: c.endpoint || '' }) : { mode: 'local', model: c.model || engine.models[0], runtime: c.runtime || engine.runtimes[0], host: c.host || 'http://127.0.0.1:8000', workers: c.workers || 2 })}>
                <Radio value="local" label="On-premises" />
                <Radio value="api" label="API" />
              </RadioGroup>
            </Field>
            {c.mode === 'local' ? (
              <div className="form-grid">
                <Field label="Model">
                  <Dropdown value={c.model} selectedOptions={[c.model]} onOptionSelect={(_, d) => set({ model: d.optionValue })}>
                    {engine.models.map((m) => <Option key={m}>{m}</Option>)}
                  </Dropdown>
                </Field>
                <Field label="Runtime">
                  <Dropdown value={c.runtime} selectedOptions={[c.runtime]} onOptionSelect={(_, d) => set({ runtime: d.optionValue })}>
                    {engine.runtimes.map((m) => <Option key={m}>{m}</Option>)}
                  </Dropdown>
                </Field>
                <Field label="Host" required><Input value={c.host} onChange={(_, d) => set({ host: d.value })} /></Field>
                <Field label="Workers"><SpinButton min={1} max={16} value={c.workers} onChange={num('workers')} /></Field>
              </div>
            ) : (
              <>
                <div className="form-grid">
                  <Field label="Provider">
                    <Dropdown value={c.provider} selectedOptions={[c.provider]} onOptionSelect={(_, d) => set({ provider: d.optionValue })}>
                      {engine.providers.map((m) => <Option key={m}>{m}</Option>)}
                    </Dropdown>
                  </Field>
                  <Field label="Endpoint URL" required validationState={badUrl ? 'error' : 'none'} validationMessage={badUrl ? 'Use an HTTPS endpoint' : undefined}>
                    <Input type="url" value={c.endpoint} placeholder="https://" onChange={(_, d) => set({ endpoint: d.value })} />
                  </Field>
                  <Field label="Authentication">
                    <Dropdown value={AUTH[c.auth]} selectedOptions={[c.auth]} onOptionSelect={(_, d) => set({ auth: d.optionValue })}>
                      {Object.entries(AUTH).map(([k, v]) => <Option key={k} value={k}>{v}</Option>)}
                    </Dropdown>
                  </Field>
                  {(c.auth === 'apikey' || c.auth === 'bearer') && (
                    <Field label={AUTH[c.auth]} hint="Stored in the server vault, never shown again">
                      <Input type="password" value={c.key} autoComplete="off" onChange={(_, d) => set({ key: d.value })} />
                    </Field>
                  )}
                  {c.auth === 'oauth' && (
                    <>
                      <Field label="Tenant ID"><Input value={c.tenant || ''} onChange={(_, d) => set({ tenant: d.value })} /></Field>
                      <Field label="Client ID"><Input value={c.client || ''} onChange={(_, d) => set({ client: d.value })} /></Field>
                      <Field label="Client secret" hint="Stored in the server vault, never shown again"><Input type="password" value={c.key} autoComplete="off" onChange={(_, d) => set({ key: d.value })} /></Field>
                    </>
                  )}
                  {c.auth === 'iam' && (
                    <Field label="Role ARN or service account" hint="Credentials come from the host identity"><Input value={c.role || ''} onChange={(_, d) => set({ role: d.value })} /></Field>
                  )}
                  <Field label="Model or deployment ID"><Input value={c.modelId} onChange={(_, d) => set({ modelId: d.value })} /></Field>
                  <Field label="Monthly budget (USD)" hint="0 means no cap"><SpinButton min={0} max={100000} step={50} value={c.budget} onChange={num('budget')} /></Field>
                </div>
                <Accordion collapsible>
                  <AccordionItem value="adv">
                    <AccordionHeader>Advanced</AccordionHeader>
                    <AccordionPanel>
                      <div className="form-grid">
                        <Field label="Timeout (s)"><SpinButton min={5} max={600} value={c.timeout} onChange={num('timeout')} /></Field>
                        <Field label="Retries"><SpinButton min={0} max={10} value={c.retries} onChange={num('retries')} /></Field>
                        <Field label="Rate limit (requests per minute)"><SpinButton min={1} max={10000} value={c.rate} onChange={num('rate')} /></Field>
                        <Field label="Custom headers" hint="One per line, Name: value"><Textarea value={c.headers} resize="vertical" onChange={(_, d) => set({ headers: d.value })} /></Field>
                      </div>
                    </AccordionPanel>
                  </AccordionItem>
                </Accordion>
              </>
            )}
          </DialogContent>
          <DialogActions>
            <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
            <Button appearance="primary" disabled={!ok} onClick={() => onSave({ ...c, id: c.id || `${engine.id}-${Date.now()}` })}>Save</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

export default function Engines() {
  const { notify, profiles } = useApp();
  const [conns, setConns] = useState(INITIAL_CONNS);
  const [cfg, setCfg] = useState(SETTINGS);
  const [tab, setTab] = useState(null);
  const [testing, setTesting] = useState(null);
  const [editing, setEditing] = useState(null);
  const e = E[tab] || {};
  const chain = conns[tab] || [];
  const set = (patch) => setCfg({ ...cfg, ...patch });
  const num = (k) => (_, d) => set({ [k]: d.value ?? Number(d.displayValue) });
  const setChain = (next) => setConns({ ...conns, [tab]: next });
  const move = (i, dir) => { const n = [...chain]; [n[i], n[i + dir]] = [n[i + dir], n[i]]; setChain(n); };
  const profileNames = profiles.map((p) => p.name);

  const test = (c) => {
    if (c.mode === 'api' && ['apikey', 'bearer', 'oauth'].includes(c.auth) && !c.key && c.status !== 'connected') {
      setChain(chain.map((x) => (x.id === c.id ? { ...x, status: 'offline' } : x)));
      notify(`${c.name}: credential missing`, 'error');
      return;
    }
    setTesting(c.id);
    setTimeout(() => {
      setTesting(null);
      setConns((all) => ({ ...all, [tab]: all[tab].map((x) => (x.id === c.id ? { ...x, status: 'connected', latency: x.latency || (x.mode === 'api' ? '820 ms' : '200 ms') } : x)) }));
      notify(`${c.name} connected`);
    }, 900);
  };

  return (
    <div className="page">
      <PageHeader picto={tab ? (E[tab]?.picto || 'locked_with_key') : 'gear'} title={tab ? (tab === 'privacy' ? 'Privacy and cost' : e.name) : 'AI and engines'}
        description={tab ? (tab === 'privacy' ? 'Control what leaves your network and what it costs.' : e.tech) : 'Each step of reading a document runs on an engine. Connect it locally or through an API, and add fallbacks.'}
        crumbs={tab && <Crumbs items={[['AI and engines', ['engines']], [tab === 'privacy' ? 'Privacy and cost' : e.name]]} />}>
        {tab && <Button appearance="primary" icon={<SaveRegular />} onClick={() => notify('Engine settings saved')}>Save</Button>}
        {tab && <Button onClick={() => setTab(null)}>All engines</Button>}
      </PageHeader>

      {!tab && (
        <div className="grid-3">
          {[...ENGINES, { id: 'privacy', name: 'Privacy and cost', tech: 'Redaction, API logs, and budgets', picto: "locked_with_key" }].map((x) => {
            const primary = conns[x.id]?.[0];
            return (
              <Card key={x.id} className="card engine-card lift" as="button" onClick={() => setTab(x.id)} aria-label={`${x.name} settings`}>
                <CardHeader image={<Picto name={x.picto} size={44} />} header={<Text weight="semibold" size={400}>{x.name}</Text>} description={<Caption1>{x.tech}</Caption1>} action={<ChevronRightRegular aria-hidden />} />
                {primary ? (
                  <div className="row between">
                    <Caption1>{`${primary.name}${conns[x.id].length > 1 ? `, ${conns[x.id].length - 1} fallback` : ''}`}</Caption1>
                    <StatusBadge status={primary.status} />
                  </div>
                ) : <Caption1>PII redaction on, 2 API budgets</Caption1>}
              </Card>
            );
          })}
        </div>
      )}

      {tab && tab !== 'privacy' && (
        <div role="tabpanel" aria-label={e.name} className="stack">
          <Card className="card">
            <div className="row between">
              <Title as="h2" size="section">Connections</Title>
              <Button icon={<AddRegular />} onClick={() => setEditing({ name: '', mode: 'api', ...api({ provider: e.providers[0], endpoint: '' }) })}>Add connection</Button>
            </div>
            <div className="table-wrap">
              <Table aria-label={`${e.name} connections in fallback order`} className="responsive auto-table">
                <TableHeader>
                  <TableRow>
                    <TableHeaderCell>Order</TableHeaderCell><TableHeaderCell>Connection</TableHeaderCell><TableHeaderCell>Source</TableHeaderCell>
                    <TableHeaderCell>Status</TableHeaderCell><TableHeaderCell>Latency</TableHeaderCell><TableHeaderCell>Budget</TableHeaderCell><TableHeaderCell><span className="sr-only">Actions</span></TableHeaderCell>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {chain.map((c, i) => (
                    <TableRow key={c.id}>
                      <TableCell data-label="Order">{i === 0 ? 'Primary' : `Fallback ${i}`}</TableCell>
                      <TableCell data-label="Connection">
                        <div className="list-main"><Text weight="semibold">{c.name}</Text><Text size={200} className="muted">{c.mode === 'api' ? `${c.provider}${c.modelId ? `, ${c.modelId}` : ''}` : `${c.model}, ${c.runtime}`}</Text></div>
                      </TableCell>
                      <TableCell data-label="Source">{c.mode === 'api' ? `API, ${AUTH[c.auth]}` : 'On-premises'}</TableCell>
                      <TableCell data-label="Status"><StatusBadge status={c.status} /></TableCell>
                      <TableCell data-label="Latency">{c.latency}</TableCell>
                      <TableCell data-label="Budget">
                        {c.mode === 'api' && c.budget > 0 ? (
                          <Field validationState={c.spent / c.budget >= 0.8 ? 'error' : 'none'} validationMessage={`USD ${c.spent} of ${c.budget}`}>
                            <ProgressBar value={c.spent / c.budget} color={c.spent / c.budget >= 0.8 ? 'error' : 'brand'} aria-label={`${c.name} budget used`} />
                          </Field>
                        ) : ''}
                      </TableCell>
                      <TableCell>
                        <div className="row nowrap">
                          <Tooltip content="Move up" relationship="label"><Button appearance="subtle" size="small" icon={<ArrowUpRegular />} disabled={i === 0} onClick={() => move(i, -1)} /></Tooltip>
                          <Tooltip content="Move down" relationship="label"><Button appearance="subtle" size="small" icon={<ArrowDownRegular />} disabled={i === chain.length - 1} onClick={() => move(i, 1)} /></Tooltip>
                          <Tooltip content="Test connection" relationship="label"><Button appearance="subtle" size="small" icon={testing === c.id ? <Spinner size="extra-tiny" /> : <PlugConnectedRegular />} disabled={!!testing} onClick={() => test(c)} /></Tooltip>
                          <Tooltip content="Edit" relationship="label"><Button appearance="subtle" size="small" icon={<EditRegular />} onClick={() => setEditing(c)} /></Tooltip>
                          <Tooltip content="Remove" relationship="label"><Button appearance="subtle" size="small" icon={<DeleteRegular />} disabled={chain.length === 1} onClick={() => setChain(chain.filter((x) => x.id !== c.id))} /></Tooltip>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </Card>

          <Card className="card">
            <Title as="h2" size="section">Settings</Title>
            {tab === 'router' && (
              <div className="form-grid">
                <SliderField label="Minimum confidence" value={cfg.minDocConf} min={0.5} max={0.99} step={0.01} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ minDocConf: v })} />
                <Field label="When the type is unclear">
                  <RadioGroup value={cfg.unknown} onChange={(_, d) => set({ unknown: d.value })}>
                    <Radio value="review" label="Send to review" />
                    <Radio value="general" label="Use a general profile" />
                  </RadioGroup>
                </Field>
                <PairTable label="Keyword and vendor rules, checked before the model" cols={['Text contains', 'Profile']} rows={cfg.rules} options={profileNames} onChange={(rules) => set({ rules })} />
                <PairTable label="Upload source rules" cols={['Source', 'Profile']} rows={cfg.sources} options={profileNames} onChange={(sources) => set({ sources })} />
              </div>
            )}
            {tab === 'vector' && (
              <div className="form-grid">
                <SliderField label="Text coverage to treat a page as digital" value={cfg.minCoverage} min={50} max={100} step={5} fmt={(v) => `${v}%`} onChange={(v) => set({ minCoverage: v })} />
                <Field label="Line merge tolerance (pt)"><SpinButton min={0} max={10} value={cfg.tolerance} onChange={num('tolerance')} /></Field>
                <Switch label="Send image-only pages to text recognition" checked={cfg.mixedPages} onChange={(_, d) => set({ mixedPages: d.checked })} />
              </div>
            )}
            {tab === 'upscaler' && (
              <div className="form-grid">
                <Field label="Mode">
                  <RadioGroup value={cfg.upMode} onChange={(_, d) => set({ upMode: d.value })}>
                    <Radio value="auto" label="Automatic, below a DPI threshold" />
                    <Radio value="always" label="Always" />
                    <Radio value="off" label="Off" />
                  </RadioGroup>
                </Field>
                <Field label="Enhance below (DPI)"><SpinButton min={72} max={300} step={10} value={cfg.belowDpi} disabled={cfg.upMode !== 'auto'} onChange={num('belowDpi')} /></Field>
                <Field label="Maximum scale">
                  <Dropdown value={{ x2: '2x', x4: '4x' }[cfg.maxScale]} selectedOptions={[cfg.maxScale]} disabled={cfg.upMode === 'off'} onOptionSelect={(_, d) => set({ maxScale: d.optionValue })}>
                    <Option value="x2">2x</Option><Option value="x4">4x</Option>
                  </Dropdown>
                </Field>
              </div>
            )}
            {tab === 'layout' && (
              <div className="form-grid">
                <SliderField label="Detection threshold" value={cfg.threshold} min={0.1} max={0.95} step={0.05} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ threshold: v })} />
                <Field label="Maximum pages per document"><SpinButton min={1} max={500} value={cfg.maxPages} onChange={num('maxPages')} /></Field>
                <Switch label="Detect table structure" checked={cfg.tables} onChange={(_, d) => set({ tables: d.checked })} />
                <Switch label="Detect reading order" checked={cfg.order} onChange={(_, d) => set({ order: d.checked })} />
              </div>
            )}
            {tab === 'ocr' && (
              <div className="form-grid">
                <Field label="Render DPI"><SpinButton min={72} max={600} step={25} value={cfg.dpi} onChange={num('dpi')} /></Field>
                <SliderField label="Text recognition trust threshold" value={cfg.trust} min={0.5} max={1} step={0.01} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ trust: v })} />
                <Field label="Recognition languages">
                  <Dropdown multiselect value={cfg.langs.join(', ')} selectedOptions={cfg.langs} onOptionSelect={(_, d) => set({ langs: d.selectedOptions })}>
                    {['English', 'Hindi', 'Gujarati', 'German', 'Chinese'].map((l) => <Option key={l}>{l}</Option>)}
                  </Dropdown>
                </Field>
              </div>
            )}
            {tab === 'llm' && (
              <div className="form-grid">
                <SliderField label="Temperature" value={cfg.temperature} min={0} max={1} step={0.1} fmt={(v) => v.toFixed(1)} onChange={(v) => set({ temperature: v })} />
                <Field label="Maximum output tokens"><SpinButton min={256} max={32000} step={256} value={cfg.maxTokens} onChange={num('maxTokens')} /></Field>
                <Switch label="Structured JSON output" checked={cfg.json} onChange={(_, d) => set({ json: d.checked })} />
                <Field label="System prompt" className="span-2"><Textarea value={cfg.prompt} resize="vertical" rows={4} onChange={(_, d) => set({ prompt: d.value })} /></Field>
              </div>
            )}
            {tab === 'decision' && (
              <div className="form-grid">
                <SliderField label="Accept threshold" value={cfg.accept} min={0.5} max={1} step={0.01} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ accept: v })} />
                <Switch label="Accept automatically when all checks pass" checked={cfg.autoAccept} onChange={(_, d) => set({ autoAccept: d.checked })} />
                <Checks label="Validation checks" options={['Charges add up', 'Tax sum', 'Container number (ISO 6346)', 'MAWB check digit', 'Date range', 'GSTIN format']} value={cfg.checks} onChange={(v) => set({ checks: v })} />
              </div>
            )}
            {tab === 'evaluator' && (
              <div className="form-grid">
                <SliderField label="Documents checked" value={cfg.sample} min={0} max={100} step={5} fmt={(v) => `${v}%`} onChange={(v) => set({ sample: v })} />
                <SliderField label="Pass score" value={cfg.pass} min={0.5} max={1} step={0.01} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ pass: v })} />
                <Checks label="Criteria" options={['Value is on the document', 'Format is valid', 'Fields agree with each other', 'Matches the profile type']} value={cfg.criteria} onChange={(v) => set({ criteria: v })} />
                <Field label="Below the pass score">
                  <RadioGroup value={cfg.onFail} onChange={(_, d) => set({ onFail: d.value })}>
                    <Radio value="review" label="Send to review" />
                    <Radio value="block" label="Block export" />
                  </RadioGroup>
                </Field>
              </div>
            )}
          </Card>
        </div>
      )}

      {tab === 'privacy' && (
        <div role="tabpanel" aria-label="Privacy" className="stack">
          <Card className="card">
            <Title as="h2" size="section">Hide personal data before API calls</Title>
            <div className="form-grid">
              <Checks label="Redact" options={['GSTIN', 'PAN', 'Bank account', 'IFSC', 'Phone', 'Email', 'Person name']} value={cfg.redact} onChange={(v) => set({ redact: v })} />
              <Field label="Method">
                <RadioGroup value={cfg.redactMode} onChange={(_, d) => set({ redactMode: d.value })}>
                  <Radio value="mask" label="Mask, then restore in the result" />
                  <Radio value="drop" label="Remove" />
                </RadioGroup>
              </Field>
            </div>
          </Card>
          <Card className="card">
            <Title as="h2" size="section">API logs</Title>
            <div className="form-grid">
              <Switch label="Keep request contents" checked={cfg.logPayloads} onChange={(_, d) => set({ logPayloads: d.checked })} />
              <Field label="Keep logs for (days)"><SpinButton min={1} max={365} value={cfg.retention} onChange={num('retention')} /></Field>
            </div>
          </Card>
        </div>
      )}

      {editing && (
        <ConnectionDialog engine={e} conn={editing} onClose={() => setEditing(null)}
          onSave={(c) => { setChain(editing.id ? chain.map((x) => (x.id === c.id ? c : x)) : [...chain, c]); setEditing(null); notify(`${c.name} saved`); }} />
      )}
    </div>
  );
}
