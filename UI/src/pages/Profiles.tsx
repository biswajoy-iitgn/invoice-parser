import { useEffect, useState } from 'react';
import {
  Button, Card, CardHeader, CardFooter, Text, Caption1, TabList, Tab, Checkbox, Switch, Field, Input, Dropdown, Option, Combobox, RadioGroup, Radio, Link, MessageBar, MessageBarBody, MessageBarActions,
  Textarea, Tooltip, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
} from '@fluentui/react-components';
import {
  AddRegular, SaveRegular, DeleteRegular, EditRegular, ReceiptRegular, ClipboardTextLtrRegular, VehicleTruckProfileRegular, DocumentRegular,
  DocumentSignatureRegular, MailRegular, DocumentDataRegular,
} from '@fluentui/react-icons';
import { useApp, PageHeader, EmptyState, Title, Glyph, RHead, RHeader, RBody, RRow, RCell, RTable, count } from '../ui';
import { TEMPLATES, WHEN, ACTION, fieldsOf, appliesTo } from '../data';
import { whereItRuns } from '../engines';
import { api, SNAPSHOT } from '../api';
import type { ModelCatalog, ModelEntry } from '../api';
import { Crumbs } from './Runs';
import { useCardStyles, useControlStyles } from '../styles';
import type { ExportTarget, FieldType, Preset, Profile, ProfileField, ProfileModels, Workflow } from '../types';

const ICONS: Record<string, typeof ReceiptRegular> = {
  invoice: ReceiptRegular, po: ClipboardTextLtrRegular, dn: VehicleTruckProfileRegular, contract: DocumentSignatureRegular, letter: MailRegular, datasheet: DocumentDataRegular,
};
const TYPES: FieldType[] = ['Text', 'Long text', 'Number', 'Amount', 'Date', 'Yes/No', 'Table'];
const READS: Record<Preset, string> = { fast: 'read for speed', balanced: 'balanced speed and accuracy', accurate: 'read for accuracy' };
const EXPORT_SHORT: Record<ExportTarget, string> = { csv: 'CSV', excel: 'Excel', database: 'a database' };
const EXPORTS: Record<ExportTarget, string> = { csv: 'CSV file', excel: 'Excel workbook', database: 'Database' };
const LANGS = ['English', 'Hindi', 'Gujarati', 'German', 'Chinese'];
const LEAVES = { always: 'Yes', fallback: 'Only on fallback', never: 'No' } as const;

// Switching one profile off an "all profiles" automation keeps it on for the others.
const toggleFor = (w: Workflow, id: string, on: boolean, all: string[]): Workflow['profiles'] => {
  const list = w.profiles === 'all' ? all : w.profiles;
  const next = on ? [...new Set([...list, id])] : list.filter((x) => x !== id);
  return all.every((x) => next.includes(x)) ? 'all' : next;
};

export function Profiles() {
  const { profiles, setProfiles, go, log } = useApp();
  const cs = useCardStyles();
  const [draft, setDraft] = useState<{ name: string; docType: string; template: string } | null>(null);
  const create = () => {
    if (!draft) return;
    const id = `p-${Date.now()}`;
    const name = draft.name.trim();
    setProfiles([...profiles, {
      id, name, docType: draft.docType.trim() || name, template: draft.template, preset: 'balanced', evaluator: true, onPremOnly: false,
      linked: [], languages: ['English'], exports: ['excel'], owner: '', fields: fieldsOf(draft.template),
    }]);
    log('Created profile', name);
    setDraft(null);
    go('profile', id);
  };
  const start = () => setDraft({ name: '', docType: '', template: 'blank' });
  return (
    <div className="page">
      <PageHeader title="Profiles" description="A profile says what kind of document to expect, which fields to read, and where results go. Any kind of document: invoices, contracts, letters, datasheets.">
        <Button appearance="primary" icon={<AddRegular />} onClick={start}>New profile</Button>
      </PageHeader>
      {profiles.length === 0 ? (
        <EmptyState picto="card_index_dividers" title="No profiles yet" body="Create a profile for each kind of document you process.">
          <Button appearance="primary" onClick={start}>New profile</Button>
        </EmptyState>
      ) : (
        <div className="grid-3">
          {profiles.map((p) => {
            const n = p.fields.filter((f) => f.enabled).length;
            return (
              <Card key={p.id} className="card profile-card">
                <CardHeader image={<Glyph icon={ICONS[p.template] || DocumentRegular} />} header={<Text as="h2" weight="semibold" size={400}>{p.name}</Text>}
                  description={<Caption1>{p.owner ? `${p.docType}, used by ${p.owner}` : p.docType}</Caption1>} />
                <Text>{p.engine === 'general' ? 'Every value, table and the text' : n ? `${count(n, 'field', 'fields')}, ${READS[p.preset]}` : 'No fields yet'}</Text>
                <Caption1 className="muted">{`Exports to ${p.exports.map((e) => EXPORT_SHORT[e]).join(' and ') || 'nothing yet'}`}</Caption1>
                {p.models && <Caption1 className="muted">{modelLine(p)}</Caption1>}
                {p.onPremOnly && <Caption1 className="muted">Stays on-premises</Caption1>}
                <CardFooter className={cs.footer}><Button onClick={() => go('profile', p.id)}>Edit profile</Button></CardFooter>
              </Card>
            );
          })}
        </div>
      )}
      <Dialog open={!!draft} onOpenChange={(_, d) => !d.open && setDraft(null)}>
        <DialogSurface>
          {draft && (
            <form onSubmit={(e) => { e.preventDefault(); if (draft.name.trim()) create(); }}>
              <DialogBody>
                <DialogTitle>New profile</DialogTitle>
                <DialogContent className="form">
                  <Field label="Name" required><Input value={draft.name} onChange={(_, d) => setDraft({ ...draft, name: d.value })} placeholder="For example, Mill test certificate" /></Field>
                  <Field label="Kind of document" hint="Anything you read: a bill of entry, an NDA, a test certificate">
                    <Input value={draft.docType} onChange={(_, d) => setDraft({ ...draft, docType: d.value })} placeholder={draft.name || 'Same as the name'} />
                  </Field>
                  <Field label="Start with">
                    <RadioGroup value={draft.template} onChange={(_, d) => {
                      const was = TEMPLATES[draft.template].name;
                      setDraft({ ...draft, template: d.value, docType: !draft.docType || draft.docType === was ? (d.value === 'blank' ? '' : TEMPLATES[d.value].name) : draft.docType });
                    }}>
                      {Object.entries(TEMPLATES).map(([k, t]) => (
                        <Radio key={k} value={k} label={k === 'blank' ? "No fields, I'll add my own" : `${t.name} fields (${t.fields.length})`} />
                      ))}
                    </RadioGroup>
                  </Field>
                </DialogContent>
                <DialogActions>
                  <Button appearance="primary" type="submit" disabled={!draft.name.trim()}>Create</Button>
                  <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
                </DialogActions>
              </DialogBody>
            </form>
          )}
        </DialogSurface>
      </Dialog>
    </div>
  );
}

const MODEL_LABEL: Record<keyof ProfileModels, string> = { reader: 'AI model', tables: 'Table reader' };
const nameOf = (list: ModelEntry[], id?: string) => list.find((m) => m.id === id)?.name ?? id ?? '';
const modelLine = (p: Profile) => {
  const bits = [p.models?.reader && nameOf(SNAPSHOT.models.readers, p.models.reader), p.editableModels?.includes('tables') && p.models?.tables && nameOf(SNAPSHOT.models.tables, p.models.tables)];
  return `Reads with ${bits.filter(Boolean).join(' and ') || 'the invoice reader'}`;
};
const licence = (m: ModelEntry) => [m.licence, m.commercial ? 'commercial use OK' : 'research use only'].filter(Boolean).join(', ');

/** Changing how a profile reads is a choice of models: one dropdown each, with the licence and what was measured. */
function ModelPicker({ p, set }: { p: Profile; set: (patch: Partial<Profile>) => void }) {
  const [catalog, setCatalog] = useState<ModelCatalog>(SNAPSHOT.models);
  useEffect(() => { api.models().then(setCatalog).catch(() => { /* offline: the bundled catalog stands */ }); }, []);
  const kinds = (p.editableModels ?? []) as (keyof ProfileModels)[];
  if (!p.engine) return null;
  if (!kinds.length) return <MessageBar intent="info"><MessageBarBody>This profile reads with the freight invoice model. Its fields are fixed by that model, so there is nothing to switch.</MessageBarBody></MessageBar>;
  return (
    <>
      <Title as="h2" size="section">Models</Title>
      <div className="form-grid">
        {kinds.map((k) => {
          const list = catalog[k === 'reader' ? 'readers' : 'tables'];
          const chosen = list.find((m) => m.id === (p.models?.[k] ?? list.find((m2) => m2.default)?.id));
          return (
            <Field key={k} label={MODEL_LABEL[k]} hint={chosen ? `${licence(chosen)}. ${chosen.note ?? ''}`.trim() : undefined}>
              <Dropdown value={chosen?.name ?? ''} selectedOptions={chosen ? [chosen.id] : []} onOptionSelect={(_, d) => set({ models: { ...p.models, [k]: d.optionValue } })}>
                {list.map((m) => <Option key={m.id} value={m.id} text={m.name}>{`${m.name}${m.default ? ' (default)' : ''}`}</Option>)}
              </Dropdown>
            </Field>
          );
        })}
      </div>
    </>
  );
}

type FieldDraft = { key?: string; label: string; type: FieldType; group: string; hint: string; columns: string };
const toDraft = (f?: ProfileField, group = 'Fields'): FieldDraft =>
  f ? { key: f.key, label: f.label, type: f.type, group: f.group, hint: f.hint ?? '', columns: (f.columns ?? []).join(', ') } : { label: '', type: 'Text', group, hint: '', columns: '' };

export function ProfileEditor() {
  const { profiles, setProfiles, route, go, notify, tables, workflows, setWorkflows, engines, connections, log } = useApp();
  const saved = profiles.find((p) => p.id === route.id);
  const [p, setP] = useState<Profile | undefined>(saved);
  const [tab, setTab] = useState(saved?.engine === 'general' ? 'reading' : 'fields');
  const [field, setField] = useState<FieldDraft | null>(null);
  const [removing, setRemoving] = useState(false);
  const cc = useControlStyles();
  if (!p || !saved) {
    return (
      <div className="page">
        <EmptyState picto="card_index_dividers" title="This profile isn't available" body="It may have been deleted.">
          <Button appearance="primary" onClick={() => go('profiles')}>Go to profiles</Button>
        </EmptyState>
      </div>
    );
  }
  const dirty = JSON.stringify(p) !== JSON.stringify(saved);
  const set = (patch: Partial<Profile>) => setP({ ...p, ...patch });
  const patchField = (key: string, patch: Partial<ProfileField>) => set({ fields: p.fields.map((f) => (f.key === key ? { ...f, ...patch } : f)) });
  const save = async () => {
    // a change of models goes to the service, which keeps it in the profile's file; the rest of the profile is kept here
    let kept = true;
    if (p.serverId && p.editableModels?.length && JSON.stringify(p.models) !== JSON.stringify(saved.models)) {
      try { await api.saveModels(p.serverId, Object.fromEntries(p.editableModels.map((k) => [k, p.models?.[k]]).filter(([, v]) => v))); } catch (e) { kept = false; notify("The service didn't take the model change", 'warning', (e as Error).message); }
    }
    setProfiles(profiles.map((x) => (x.id === p.id ? p : x)));
    log('Saved profile', p.name);
    if (kept) notify(`${p.name} saved`);
  };
  const groups = [...new Set(p.fields.map((f) => f.group))];
  const saveField = () => {
    if (!field) return;
    const columns = field.columns.split(',').map((c) => c.trim()).filter(Boolean);
    const next: ProfileField = {
      key: field.key ?? `f_${field.label.trim().toLowerCase().replace(/\W+/g, '_')}_${Date.now().toString(36)}`, label: field.label.trim(), type: field.type,
      group: field.group.trim() || 'Fields', enabled: true, required: false,
      ...(field.hint.trim() && { hint: field.hint.trim() }), ...(field.type === 'Table' && { columns }),
    };
    const old = p.fields.find((f) => f.key === field.key);
    set({ fields: old ? p.fields.map((f) => (f.key === old.key ? { ...next, enabled: old.enabled, required: old.required } : f)) : [...p.fields, next] });
    setField(null);
  };
  const steps = whereItRuns(engines, p);

  return (
    <div className="page">
      <PageHeader title={p.name} crumbs={<Crumbs items={[['Profiles', ['profiles']], [p.name]]} />}>
        <Button appearance="primary" icon={<SaveRegular />} disabled={!dirty} onClick={save}>Save</Button>
        <Button appearance="subtle" icon={<DeleteRegular />} onClick={() => setRemoving(true)}>Delete</Button>
      </PageHeader>
      {dirty && (
        <MessageBar intent="info">
          <MessageBarBody>You have unsaved changes to this profile.</MessageBarBody>
          <MessageBarActions><Button onClick={save}>Save</Button><Button onClick={() => setP(saved)}>Discard</Button></MessageBarActions>
        </MessageBar>
      )}

      <div className="form-grid">
        <Field label="Name" required><Input value={p.name} onChange={(_, d) => set({ name: d.value })} /></Field>
        <Field label="Kind of document"><Input value={p.docType} onChange={(_, d) => set({ docType: d.value })} /></Field>
      </div>

      <div className="tabs-scroll"><TabList selectedValue={tab} onTabSelect={(_, d) => setTab(String(d.value))}>
        <Tab value="fields">Fields</Tab>
        <Tab value="reading">Reading</Tab>
        <Tab value="output">Where results go</Tab>
      </TabList></div>

      {tab === 'fields' && p.engine === 'general' && (
        <div role="tabpanel" aria-label="Fields" className="stack">
          <MessageBar intent="info"><MessageBarBody>This profile reads everything printed: every labelled value, every table and the full text. There is no field list to keep. Use a profile with fields when a document type needs required values.</MessageBarBody></MessageBar>
        </div>
      )}
      {tab === 'fields' && p.engine !== 'general' && (
        <div role="tabpanel" aria-label="Fields" className="stack">
          <div className="row between">
            <Text>{p.fields.length ? `${p.fields.filter((f) => f.enabled).length} of ${count(p.fields.length, 'field', 'fields')} included` : 'No fields yet'}</Text>
            <Button icon={<AddRegular />} onClick={() => setField(toDraft(undefined, groups[groups.length - 1]))}>Add field</Button>
          </div>
          {p.fields.length === 0 && (
            <EmptyState compact picto="card_index_dividers" title="Say what to read" body="Add a field for each value you want from this kind of document. Describe where it is, and the AI uses that.">
              <Button appearance="primary" icon={<AddRegular />} onClick={() => setField(toDraft())}>Add field</Button>
            </EmptyState>
          )}
          {groups.map((g) => (
            <Card key={g} className="card">
              <Title as="h2" size="section">{g}</Title>
              <RTable size="small" aria-label={g} className="field-table">
                <RHeader><RRow>
                  <RHead className={cc.colCheck}>Include</RHead><RHead>Field</RHead><RHead className={cc.colType}>Type</RHead><RHead className={cc.colSwitch}>Required</RHead>
                  <RHead className={cc.colCheck}><span className="sr-only">Actions</span></RHead>
                </RRow></RHeader>
                <RBody>
                  {p.fields.filter((f) => f.group === g).map((f) => (
                    <RRow key={f.key}>
                      <RCell data-label="Include"><Checkbox checked={f.enabled} aria-label={`Include ${f.label}`} onChange={(_, d) => patchField(f.key, { enabled: d.checked === true, required: d.checked === true && f.required })} /></RCell>
                      <RCell data-label="Field"><div className="cell-stack"><Text>{f.label}</Text>{f.hint && <Caption1 className="muted">{f.hint}</Caption1>}</div></RCell>
                      <RCell data-label="Type">{f.type === 'Table' && f.columns?.length ? `Table: ${f.columns.join(', ')}` : f.type}</RCell>
                      <RCell data-label="Required"><Switch checked={f.required} disabled={!f.enabled} aria-label={`${f.label} required`} onChange={(_, d) => patchField(f.key, { required: d.checked })} /></RCell>
                      <RCell>
                        <div className="row nowrap">
                          <Tooltip content={`Edit ${f.label}`} relationship="label"><Button appearance="subtle" size="small" icon={<EditRegular />} onClick={() => setField(toDraft(f))} /></Tooltip>
                          <Tooltip content={`Remove ${f.label}`} relationship="label"><Button appearance="subtle" size="small" icon={<DeleteRegular />} onClick={() => set({ fields: p.fields.filter((x) => x.key !== f.key) })} /></Tooltip>
                        </div>
                      </RCell>
                    </RRow>
                  ))}
                </RBody>
              </RTable>
            </Card>
          ))}
        </div>
      )}

      {tab === 'reading' && (
        <div role="tabpanel" aria-label="Reading" className="stack">
          <ModelPicker p={p} set={set} />
          <div className="form-grid">
            <Field label="Quality">
              <RadioGroup value={p.preset} onChange={(_, d) => set({ preset: d.value as Preset })}>
                <Radio value="fast" label="Fast" />
                <Radio value="balanced" label="Balanced" />
                <Radio value="accurate" label="Most accurate" />
              </RadioGroup>
            </Field>
            <Field label="Document languages">
              <Dropdown multiselect value={p.languages.join(', ')} selectedOptions={p.languages} onOptionSelect={(_, d) => set({ languages: d.selectedOptions })}>
                {LANGS.map((l) => <Option key={l}>{l}</Option>)}
              </Dropdown>
            </Field>
            <Field label="Accuracy check" hint="A second AI model reviews every value the first one found">
              <Switch checked={p.evaluator} label={p.evaluator ? 'On' : 'Off'} onChange={(_, d) => set({ evaluator: d.checked })} />
            </Field>
            <Field label="Data stays on-premises" hint="Cloud engines are skipped for this profile, even as a fallback">
              <Switch checked={p.onPremOnly} label={p.onPremOnly ? 'On' : 'Off'} onChange={(_, d) => set({ onPremOnly: d.checked })} />
            </Field>
          </div>
          <Title as="h2" size="section">Where each step runs</Title>
          <div className="table-card">
            <RTable layout="auto" aria-label="Where each step runs">
              <RHeader><RRow><RHead>Step</RHead><RHead>Runs on, in order</RHead><RHead>Leaves your network</RHead></RRow></RHeader>
              <RBody>
                {steps.map(({ engine, used, skipped, leaves }) => (
                  <RRow key={engine.id}>
                    <RCell data-label="Step">{engine.name}</RCell>
                    <RCell data-label="Runs on, in order">
                      <div className="cell-stack">
                        <Text>{used.length ? used.map((c) => `${c.name} (${c.mode === 'api' ? 'cloud API' : 'on-premises'})`).join(', then ') : 'Nothing: no on-premises engine is set up for this step'}</Text>
                        {skipped.length > 0 && <Caption1 className="muted">{`Skipped: ${skipped.map((c) => c.name).join(', ')}`}</Caption1>}
                      </div>
                    </RCell>
                    <RCell data-label="Leaves your network">{LEAVES[leaves]}</RCell>
                  </RRow>
                ))}
              </RBody>
            </RTable>
          </div>
          <div><Link onClick={() => go('engines')}>Change engines in AI and engines</Link></div>
        </div>
      )}

      {tab === 'output' && (
        <div role="tabpanel" aria-label="Where results go" className="stack">
          <Field label="Export as">
            {(Object.entries(EXPORTS) as [ExportTarget, string][]).map(([k, v]) => (
              <Checkbox key={k} label={v} checked={p.exports.includes(k)} onChange={(_, d) => set({ exports: d.checked ? [...p.exports, k] : p.exports.filter((e) => e !== k) })} />
            ))}
          </Field>
          {p.exports.includes('database') && (
            <Field label="Database tables" hint="People can see these tables in Databases" className="narrow-field">
              <Dropdown multiselect placeholder="Choose tables" value={p.linked.join(', ')} selectedOptions={p.linked} onOptionSelect={(_, d) => set({ linked: d.selectedOptions })}>
                {connections.flatMap((c) => Object.keys(tables[c.id] || {}).map((t) => <Option key={`${c.id}.${t}`} value={`${c.id}.${t}`}>{`${c.name}, ${t}`}</Option>))}
              </Dropdown>
            </Field>
          )}
          <Title as="h2" size="section">Automations</Title>
          <div className="table-card">
            <RTable layout="auto" aria-label="Automations for this profile">
              <RHeader><RRow><RHead>Automation</RHead><RHead>When</RHead><RHead>Then</RHead><RHead>On for this profile</RHead></RRow></RHeader>
              <RBody>
                {workflows.map((w) => (
                  <RRow key={w.id}>
                    <RCell data-label="Automation">{w.name}</RCell>
                    <RCell data-label="When">{WHEN[w.when]}</RCell>
                    <RCell data-label="Then">{ACTION[w.action]}</RCell>
                    <RCell data-label="On for this profile">
                      <Switch aria-label={`${w.name} for ${p.name}`} checked={appliesTo(w, p.id)}
                        onChange={(_, d) => {
                          setWorkflows(workflows.map((x) => (x.id === w.id ? { ...x, profiles: toggleFor(x, p.id, d.checked, profiles.map((y) => y.id)) } : x)));
                          log(`Turned automation ${d.checked ? 'on' : 'off'}`, `${w.name}, ${p.name}`);
                          notify(`${w.name} turned ${d.checked ? 'on' : 'off'} for ${p.name}`, 'info');
                        }} />
                    </RCell>
                  </RRow>
                ))}
              </RBody>
            </RTable>
          </div>
        </div>
      )}

      <Dialog open={!!field} onOpenChange={(_, d) => !d.open && setField(null)}>
        <DialogSurface>
          {field && (
            <form onSubmit={(e) => { e.preventDefault(); if (field.label.trim()) saveField(); }}>
              <DialogBody>
                <DialogTitle>{field.key ? `Edit ${field.label || 'field'}` : 'Add a field'}</DialogTitle>
                <DialogContent className="form">
                  <Field label="Field name" required><Input value={field.label} onChange={(_, d) => setField({ ...field, label: d.value })} placeholder="For example, Port of loading" /></Field>
                  <div className="form-grid">
                    <Field label="Type">
                      <Dropdown value={field.type} selectedOptions={[field.type]} onOptionSelect={(_, d) => setField({ ...field, type: d.optionValue as FieldType })}>
                        {TYPES.map((t) => <Option key={t}>{t}</Option>)}
                      </Dropdown>
                    </Field>
                    <Field label="Group">
                      <Combobox freeform value={field.group} selectedOptions={[field.group]} onOptionSelect={(_, d) => setField({ ...field, group: d.optionValue ?? field.group })}
                        onChange={(e) => setField({ ...field, group: e.target.value })}>
                        {(groups.length ? groups : ['Fields']).map((g) => <Option key={g}>{g}</Option>)}
                      </Combobox>
                    </Field>
                  </div>
                  {field.type === 'Table' && (
                    <Field label="Columns" hint="Separate with commas. One row is read for each line item, party or specification.">
                      <Input value={field.columns} onChange={(_, d) => setField({ ...field, columns: d.value })} placeholder="For example, Parameter, Value, Unit" />
                    </Field>
                  )}
                  <Field label="How to find it" hint="Optional. The AI reads this, so say where the value is or what it looks like.">
                    <Textarea value={field.hint} onChange={(_, d) => setField({ ...field, hint: d.value })} resize="vertical" placeholder="For example, the total after tax, at the bottom of the last page" />
                  </Field>
                </DialogContent>
                <DialogActions>
                  <Button appearance="primary" type="submit" disabled={!field.label.trim()}>{field.key ? 'Done' : 'Add'}</Button>
                  <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
                </DialogActions>
              </DialogBody>
            </form>
          )}
        </DialogSurface>
      </Dialog>

      <Dialog modalType="alert" open={removing} onOpenChange={(_, d) => setRemoving(d.open)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>{`Delete ${p.name}?`}</DialogTitle>
            <DialogContent>{"New uploads can't use it anymore. Documents already read keep their values."}</DialogContent>
            <DialogActions>
              <Button appearance="primary" onClick={() => { setProfiles(profiles.filter((x) => x.id !== p.id)); log('Deleted profile', p.name); notify(`${p.name} deleted`); go('profiles'); }}>Delete</Button>
              <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  );
}
