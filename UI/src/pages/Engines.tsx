import { useState } from 'react';
import {
  Button, Field, RadioGroup, Radio, Dropdown, Option, Input, SpinButton, Slider, Switch, Checkbox, Textarea,
  Accordion, AccordionItem, AccordionHeader, AccordionPanel, Card, CardHeader, Caption1, Spinner, Text, ProgressBar,
  Tooltip, MessageBar, MessageBarBody, MessageBarActions,
  Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
} from '@fluentui/react-components';
import {
  SaveRegular, PlugConnectedRegular, AddRegular, ArrowUpRegular, ArrowDownRegular, EditRegular, DeleteRegular, ChevronRightRegular, LockClosedRegular,
} from '@fluentui/react-icons';
import { useApp, PageHeader, StatusBadge, Title, Glyph, RHead, RHeader, RBody, RRow, RCell, RTable, pct } from '../ui';
import { Crumbs } from './Runs';
import { cardAction } from './Home';
import { useControlStyles } from '../styles';
import { ENGINES, AUTH, E, api, whereItRuns } from '../engines';
import type { AuthKind, Conn, Engine, EngineState, Settings } from '../engines';
import type { SpinButtonOnChangeData } from '@fluentui/react-components';

function SliderField({ label, value, min, max, step, onChange, fmt = (v: number) => String(v) }:
  { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; fmt?: (v: number) => string }) {
  return (
    <Field label={`${label}: ${fmt(value)}`}>
      <Slider min={min} max={max} step={step} value={value} input={{ className: useControlStyles().sliderInput }} aria-valuetext={String(fmt(value))} onChange={(_, d) => onChange(d.value)} />
    </Field>
  );
}

function Checks({ label, options, value, onChange }: { label: string; options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <Field label={label}>
      {options.map((o) => <Checkbox key={o} label={o} checked={value.includes(o)} onChange={(_, d) => onChange(d.checked ? [...value, o] : value.filter((x) => x !== o))} />)}
    </Field>
  );
}

function PairTable({ label, cols, rows, onChange, options }:
  { label: string; cols: [string, string]; rows: [string, string][]; onChange: (rows: [string, string][]) => void; options: string[] }) {
  return (
    <div className="stack span-2">
      <Text weight="semibold">{label}</Text>
      <div className="table-wrap">
        <RTable size="small" aria-label={label} layout="auto">
          <RHeader><RRow><RHead>{cols[0]}</RHead><RHead>{cols[1]}</RHead><RHead><span className="sr-only">Remove</span></RHead></RRow></RHeader>
          <RBody>
            {rows.map(([a, b], i) => (
              <RRow key={i}>
                <RCell data-label={cols[0]}><Input size="small" aria-label={cols[0]} value={a} onChange={(_, d) => onChange(rows.map((r, k): [string, string] => (k === i ? [d.value, r[1]] : r)))} /></RCell>
                <RCell data-label={cols[1]}>
                  <Dropdown size="small" aria-label={cols[1]} value={b} selectedOptions={[b]} onOptionSelect={(_, d) => onChange(rows.map((r, k): [string, string] => (k === i ? [r[0], d.optionValue ?? r[1]] : r)))}>
                    {options.map((o) => <Option key={o}>{o}</Option>)}
                  </Dropdown>
                </RCell>
                <RCell><Tooltip content="Remove" relationship="label"><Button appearance="subtle" size="small" icon={<DeleteRegular />} onClick={() => onChange(rows.filter((_, k) => k !== i))} /></Tooltip></RCell>
              </RRow>
            ))}
          </RBody>
        </RTable>
      </div>
      <div><Button size="small" icon={<AddRegular />} onClick={() => onChange([...rows, ['', options[0]]])}>Add</Button></div>
    </div>
  );
}

function ConnectionDialog({ engine, conn, onClose, onSave }: { engine: Engine; conn: Conn; onClose: () => void; onSave: (c: Conn) => void }) {
  const [c, setC] = useState<Conn>(conn);
  const set = (patch: Partial<Conn>) => setC({ ...c, ...patch });
  const num = (k: keyof Conn) => (_: unknown, d: SpinButtonOnChangeData) => set({ [k]: d.value ?? Number(d.displayValue) });
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
                  <Dropdown value={c.model} selectedOptions={c.model ? [c.model] : []} onOptionSelect={(_, d) => set({ model: d.optionValue })}>
                    {engine.models.map((m) => <Option key={m}>{m}</Option>)}
                  </Dropdown>
                </Field>
                <Field label="Runtime">
                  <Dropdown value={c.runtime} selectedOptions={c.runtime ? [c.runtime] : []} onOptionSelect={(_, d) => set({ runtime: d.optionValue })}>
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
                    <Dropdown value={c.provider} selectedOptions={c.provider ? [c.provider] : []} onOptionSelect={(_, d) => set({ provider: d.optionValue })}>
                      {engine.providers.map((m) => <Option key={m}>{m}</Option>)}
                    </Dropdown>
                  </Field>
                  <Field label="Endpoint URL" required validationState={badUrl ? 'error' : 'none'} validationMessage={badUrl ? 'Use an HTTPS endpoint' : undefined}>
                    <Input type="url" value={c.endpoint} placeholder="https://" onChange={(_, d) => set({ endpoint: d.value })} />
                  </Field>
                  <Field label="Authentication">
                    <Dropdown value={AUTH[c.auth ?? 'none']} selectedOptions={[c.auth ?? 'none']} onOptionSelect={(_, d) => set({ auth: d.optionValue as AuthKind })}>
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
            <Button appearance="primary" disabled={!ok} onClick={() => onSave({ ...c, id: c.id || `${engine.id}-${Date.now()}` })}>Save</Button>
            <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}

export default function Engines() {
  const { notify, profiles, route, go, engines, setEngines, engineDraft, setEngineDraft, log } = useApp();
  // Changes stay a draft until Save, across engine pages; nothing says saved before it is.
  const draft = engineDraft ?? engines;
  const { conns, cfg } = draft;
  const dirty = !!engineDraft && JSON.stringify(engineDraft) !== JSON.stringify(engines);
  const edit = (next: EngineState) => setEngineDraft(next);
  const setConns = (f: Record<string, Conn[]> | ((all: Record<string, Conn[]>) => Record<string, Conn[]>)) =>
    setEngineDraft((d) => { const cur = d ?? engines; return { ...cur, conns: typeof f === 'function' ? f(cur.conns) : f }; });
  const save = () => { setEngines(draft); setEngineDraft(null); log('Changed engine settings', tab ? (tab === 'privacy' ? 'Privacy and cost' : E[tab]?.name ?? tab) : 'AI and engines'); notify('Engine settings saved'); };
  // Each engine is its own address (#/engines/ocr), so Back and shared links work
  const tab = route.id && (E[route.id] || route.id === 'privacy') ? route.id : null;
  const setTab = (id: string | null) => go('engines', id || undefined);
  const [testing, setTesting] = useState<string | null>(null);
  const [editing, setEditing] = useState<Conn | null>(null);
  const e: Engine | undefined = tab ? E[tab] : undefined;
  const chain = (tab && conns[tab]) || [];
  const set = (patch: Partial<Settings>) => edit({ ...draft, cfg: { ...cfg, ...patch } });
  const num = (k: keyof Settings) => (_: unknown, d: SpinButtonOnChangeData) => set({ [k]: d.value ?? Number(d.displayValue) });
  const setChain = (next: Conn[]) => { if (tab) setConns({ ...conns, [tab]: next }); };
  const move = (i: number, dir: number) => { const n = [...chain]; [n[i], n[i + dir]] = [n[i + dir], n[i]]; setChain(n); };
  const profileNames = profiles.map((p) => p.name);

  const test = (c: Conn) => {
    if (c.mode === 'api' && ['apikey', 'bearer', 'oauth'].includes(c.auth ?? 'none') && !c.key && c.status !== 'connected') {
      setChain(chain.map((x) => (x.id === c.id ? { ...x, status: 'offline' } : x)));
      notify(`${c.name}: credential missing`, 'error');
      return;
    }
    setTesting(c.id);
    setTimeout(() => {
      setTesting(null);
      log('Tested engine connection', `${E[tab ?? '']?.name ?? ''}: ${c.name}`);
      notify(c.status === 'connected' ? `${c.name} answered` : `${c.name} didn't answer`, c.status === 'connected' ? 'success' : 'error', c.status === 'connected' ? undefined : 'The last fallback that answered keeps running.');
    }, 900);
  };

  return (
    <div className="page">
      <PageHeader title={tab ? (tab === 'privacy' ? 'Privacy and cost' : e?.name ?? '') : 'AI and engines'}
        description={tab ? (tab === 'privacy' ? 'Control what leaves your network and what it costs.' : e?.tech) : 'Each step of reading a document runs on an engine. Connect it locally or through an API, and add fallbacks.'}
        crumbs={tab && <Crumbs items={[['AI and engines', ['engines']], [tab === 'privacy' ? 'Privacy and cost' : e?.name ?? '']]} />}>
        <Button appearance="primary" icon={<SaveRegular />} disabled={!dirty} onClick={save}>Save</Button>
      </PageHeader>
      {dirty && (
        <MessageBar intent="info">
          <MessageBarBody>You have unsaved engine changes.</MessageBarBody>
          <MessageBarActions><Button onClick={save}>Save</Button><Button onClick={() => setEngineDraft(null)}>Discard</Button></MessageBarActions>
        </MessageBar>
      )}

      {!tab && (
        <div className="grid-3">
          {[...ENGINES, { id: 'privacy', name: 'Privacy and cost', tech: 'Redaction, API logs, and budgets', icon: LockClosedRegular }].map((x) => {
            const primary = conns[x.id]?.[0];
            return (
              <Card key={x.id} className="card engine-card lift" {...cardAction(() => setTab(x.id), `${x.name} settings`)}>
                <CardHeader image={<Glyph icon={x.icon} />} header={<Text weight="semibold" size={400}>{x.name}</Text>} description={<Caption1>{x.tech}</Caption1>} action={<ChevronRightRegular className="flip-rtl" aria-hidden />} />
                {primary ? (
                  <div className="row between">
                    <Caption1>{`${primary.name}${conns[x.id].length > 1 ? `, ${conns[x.id].length - 1} fallback` : ''}`}</Caption1>
                    <StatusBadge status={primary.status === 'offline' && conns[x.id].some((c) => c.status === 'connected') ? 'fallback' : primary.status} />
                  </div>
                ) : <Caption1>PII redaction on, 2 API budgets</Caption1>}
              </Card>
            );
          })}
        </div>
      )}

      {tab && tab !== 'privacy' && (
        <div role="tabpanel" aria-label={e?.name} className="stack">
          <Card className="card">
            <div className="row between">
              <Title as="h2" size="section">Connections</Title>
              <Button icon={<AddRegular />} onClick={() => setEditing(api({ id: '', name: '', provider: e?.providers[0], endpoint: '' }))}>Add connection</Button>
            </div>
            <div className="table-wrap">
              <RTable aria-label={`${e?.name} connections in fallback order`} layout="auto">
                <RHeader>
                  <RRow>
                    <RHead>Order</RHead><RHead>Connection</RHead><RHead>Source</RHead>
                    <RHead>Status</RHead><RHead>Latency</RHead><RHead>Budget</RHead><RHead><span className="sr-only">Actions</span></RHead>
                  </RRow>
                </RHeader>
                <RBody>
                  {chain.map((c, i) => (
                    <RRow key={c.id}>
                      <RCell data-label="Order">{i === 0 ? 'Primary' : `Fallback ${i}`}</RCell>
                      <RCell data-label="Connection">
                        <div className="list-main"><Text weight="semibold">{c.name}</Text><Text size={200} className="muted">{c.mode === 'api' ? `${c.provider}${c.modelId ? `, ${c.modelId}` : ''}` : `${c.model}, ${c.runtime}`}</Text></div>
                      </RCell>
                      <RCell data-label="Source">{c.mode === 'api' ? `API, ${AUTH[c.auth ?? 'none']}` : 'On-premises'}</RCell>
                      <RCell data-label="Status"><StatusBadge status={c.status} /></RCell>
                      <RCell data-label="Latency">{c.latency}</RCell>
                      <RCell data-label="Budget">
                        {c.mode === 'api' && c.budget && c.budget > 0 ? (
                          <Field validationState={(c.spent ?? 0) / c.budget >= 0.8 ? 'error' : 'none'} validationMessage={`USD ${c.spent} of ${c.budget}`}>
                            <ProgressBar value={(c.spent ?? 0) / c.budget} color={(c.spent ?? 0) / c.budget >= 0.8 ? 'error' : 'brand'} aria-label={`${c.name} budget used`} />
                          </Field>
                        ) : ''}
                      </RCell>
                      <RCell>
                        <div className="row nowrap">
                          <Tooltip content="Move up" relationship="label"><Button appearance="subtle" size="small" icon={<ArrowUpRegular />} disabled={i === 0} onClick={() => move(i, -1)} /></Tooltip>
                          <Tooltip content="Move down" relationship="label"><Button appearance="subtle" size="small" icon={<ArrowDownRegular />} disabled={i === chain.length - 1} onClick={() => move(i, 1)} /></Tooltip>
                          <Tooltip content="Test connection" relationship="label"><Button appearance="subtle" size="small" icon={testing === c.id ? <Spinner size="extra-tiny" /> : <PlugConnectedRegular />} disabled={!!testing} onClick={() => test(c)} /></Tooltip>
                          <Tooltip content="Edit" relationship="label"><Button appearance="subtle" size="small" icon={<EditRegular />} onClick={() => setEditing(c)} /></Tooltip>
                          <Tooltip content="Remove" relationship="label"><Button appearance="subtle" size="small" icon={<DeleteRegular />} disabled={chain.length === 1} onClick={() => setChain(chain.filter((x) => x.id !== c.id))} /></Tooltip>
                        </div>
                      </RCell>
                    </RRow>
                  ))}
                </RBody>
              </RTable>
            </div>
          </Card>

          <Card className="card">
            <Title as="h2" size="section">Settings</Title>
            {tab === 'router' && (
              <div className="form-grid">
                <SliderField label="Confidence needed to pick a profile" value={cfg.minDocConf} min={0.5} max={0.99} step={0.01} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ minDocConf: v })} />
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
                <SliderField label="Recognized text trusted from" value={cfg.trust} min={0.5} max={1} step={0.01} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ trust: v })} />
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
                <SliderField label="Ask a person to check values read below" value={cfg.accept} min={0.5} max={1} step={0.01} fmt={pct} onChange={(v) => set({ accept: v })} />
                <Field label="Approve automatically" hint="Only documents where every value is above the check level and every check passes. Off means a person approves every document.">
                  <Switch checked={cfg.autoAccept} label={cfg.autoAccept ? 'On' : 'Off'} onChange={(_, d) => set({ autoAccept: d.checked })} />
                </Field>
                <Checks label="Validation checks" options={['Charges add up', 'Tax sum', 'Container number (ISO 6346)', 'MAWB check digit', 'Date range', 'GSTIN format']} value={cfg.checks} onChange={(v) => set({ checks: v })} />
              </div>
            )}
            {tab === 'evaluator' && (
              <div className="form-grid">
                <SliderField label="Documents checked" value={cfg.sample} min={0} max={100} step={5} fmt={(v) => `${v}%`} onChange={(v) => set({ sample: v })} />
                <SliderField label="Score the accuracy check needs to pass" value={cfg.pass} min={0.5} max={1} step={0.01} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ pass: v })} />
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
            <Title as="h2" size="section">{"Where each profile's documents go"}</Title>
            <RTable layout="auto" aria-label="Where each profile's documents go">
              <RHeader><RRow><RHead>Profile</RHead><RHead>Sent to cloud APIs</RHead><RHead>Steps that use them</RHead></RRow></RHeader>
              <RBody>
                {profiles.map((p) => {
                  const steps = whereItRuns(draft, p).filter((x) => x.leaves !== 'never');
                  return (
                    <RRow key={p.id}>
                      <RCell data-label="Profile">{p.name}</RCell>
                      <RCell data-label="Sent to cloud APIs">{p.onPremOnly ? 'Never, stays on-premises' : steps.some((x) => x.leaves === 'always') ? 'Yes' : steps.length ? 'Only when a local engine is down' : 'No'}</RCell>
                      <RCell data-label="Steps that use them">{steps.map((x) => `${x.engine.name} (${x.used.filter((c) => c.mode === 'api').map((c) => c.name).join(', ')})`).join('; ') || 'None'}</RCell>
                    </RRow>
                  );
                })}
              </RBody>
            </RTable>
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

      {editing && e && (
        <ConnectionDialog engine={e} conn={editing} onClose={() => setEditing(null)}
          onSave={(c) => { setChain(editing.id ? chain.map((x) => (x.id === c.id ? c : x)) : [...chain, c]); setEditing(null); notify(`${c.name} saved`); }} />
      )}
    </div>
  );
}
