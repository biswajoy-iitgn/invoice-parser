import { useState } from 'react';
import {
  Button, Card, CardHeader, CardFooter, Text, Caption1, TabList, Tab, Table, TableHeader, TableRow, TableHeaderCell, TableBody, TableCell,
  Checkbox, Switch, Field, Input, Dropdown, Option, RadioGroup, Radio, Link, MessageBar, MessageBarBody, MessageBarActions,
  Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
} from '@fluentui/react-components';
import { AddRegular, SaveRegular, DeleteRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, EmptyState, Picto, Title } from '../ui';
import { FIELD_LIBRARY, CONNECTIONS, WHEN, ACTION } from '../data';
import { Crumbs } from './Runs';

const DOC_TYPES = { invoice: 'Invoice', po: 'Purchase order', dn: 'Delivery note' };
const PICTOS = { invoice: 'receipt', po: 'clipboard', dn: 'package' };
const PRESETS = { fast: 'Fast', balanced: 'Balanced', accurate: 'Most accurate' };
const EXPORT_SHORT = { csv: 'CSV', excel: 'Excel', database: 'a database' };
const EXPORTS = { csv: 'CSV file', excel: 'Excel workbook', database: 'Database' };
const LANGS = ['English', 'Hindi', 'Gujarati', 'German', 'Chinese'];

export function Profiles() {
  const { profiles, setProfiles, go } = useApp();
  const [draft, setDraft] = useState(null);
  const create = () => {
    const id = `p-${Date.now()}`;
    setProfiles([...profiles, {
      id, name: draft.name.trim(), lib: draft.lib, preset: 'balanced', aiSource: 'local', evaluator: true, onPremOnly: false, linked: [], languages: ['English'], exports: ['csv'], owner: '',
      fields: FIELD_LIBRARY[draft.lib].map(([key, label, type, group]) => ({ key, label, type, group, enabled: true, required: false })),
    }]);
    setDraft(null);
    go('profile', id);
  };
  return (
    <div className="page">
      <PageHeader picto="card_index_dividers" title="Profiles" description="A profile says what kind of document to expect, which fields to pull out, and where results go.">
        <Button appearance="primary" icon={<AddRegular />} onClick={() => setDraft({ name: '', lib: 'invoice' })}>New profile</Button>
      </PageHeader>
      {profiles.length === 0 ? (
        <EmptyState picto="card_index_dividers" title="No profiles yet" body="Create a profile for each kind of document you process.">
          <Button appearance="primary" onClick={() => setDraft({ name: '', lib: 'invoice' })}>New profile</Button>
        </EmptyState>
      ) : (
        <div className="grid-3">
          {profiles.map((p) => {
            const n = p.fields.filter((f) => f.enabled).length;
            return (
              <Card key={p.id} className="card profile-card">
                <CardHeader image={<Picto name={PICTOS[p.lib] || 'memo'} size={44} />} header={<Text as="h2" weight="semibold" size={400}>{p.name}</Text>} description={<Caption1>{DOC_TYPES[p.lib]}</Caption1>} />
                <Text>{`${n} fields, ${PRESETS[p.preset].toLowerCase()} quality`}</Text>
                <Caption1 className="muted">{`Exports to ${p.exports.map((e) => EXPORT_SHORT[e]).join(' and ')}`}</Caption1>
                {p.onPremOnly && <Caption1 className="muted">Stays on-premises</Caption1>}
                <CardFooter><Button onClick={() => go('profile', p.id)}>Edit profile</Button></CardFooter>
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
                  <Field label="Name" required><Input value={draft.name} onChange={(_, d) => setDraft({ ...draft, name: d.value })} placeholder="For example, Air freight invoice" /></Field>
                  <Field label="Document type" hint="You can turn individual fields on or off next">
                    <RadioGroup value={draft.lib} onChange={(_, d) => setDraft({ ...draft, lib: d.value })}>
                      {Object.entries(DOC_TYPES).map(([k, v]) => <Radio key={k} value={k} label={v} />)}
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

export function ProfileEditor() {
  const { profiles, setProfiles, route, go, notify, tables, workflows, setWorkflows } = useApp();
  const saved = profiles.find((p) => p.id === route.id);
  const [p, setP] = useState(saved);
  const [tab, setTab] = useState('fields');
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [newField, setNewField] = useState({ label: '', type: 'Text' });
  if (!p) {
    return (
      <div className="page">
        <EmptyState picto="card_index_dividers" title="This profile isn't available" body="It may have been deleted.">
          <Button appearance="primary" onClick={() => go('profiles')}>Go to profiles</Button>
        </EmptyState>
      </div>
    );
  }
  const dirty = JSON.stringify(p) !== JSON.stringify(saved);
  const set = (patch) => setP({ ...p, ...patch });
  const setField = (key, patch) => set({ fields: p.fields.map((f) => (f.key === key ? { ...f, ...patch } : f)) });
  const save = () => { setProfiles(profiles.map((x) => (x.id === p.id ? p : x))); notify(`${p.name} saved`); };
  const groups = [...new Set(p.fields.map((f) => f.group))];

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
        <Field label="Document type"><Input value={DOC_TYPES[p.lib]} readOnly /></Field>
      </div>

      <div className="tabs-scroll"><TabList selectedValue={tab} onTabSelect={(_, d) => setTab(d.value)}>
        <Tab value="fields">Fields</Tab>
        <Tab value="reading">Reading</Tab>
        <Tab value="output">Where results go</Tab>
      </TabList></div>

      {tab === 'fields' && (
        <div role="tabpanel" aria-label="Fields" className="stack">
          <div className="row between">
            <Text>{`${p.fields.filter((f) => f.enabled).length} of ${p.fields.length} fields are pulled out`}</Text>
            <Button icon={<AddRegular />} onClick={() => setAdding(true)}>Add field</Button>
          </div>
          {groups.map((g) => (
            <Card key={g} className="card">
              <Title as="h2" size="section">{g}</Title>
              <Table size="small" aria-label={g} className="responsive">
                <TableHeader><TableRow><TableHeaderCell>Pull out</TableHeaderCell><TableHeaderCell>Field</TableHeaderCell><TableHeaderCell>Type</TableHeaderCell><TableHeaderCell>Required</TableHeaderCell></TableRow></TableHeader>
                <TableBody>
                  {p.fields.filter((f) => f.group === g).map((f) => (
                    <TableRow key={f.key}>
                      <TableCell data-label="Pull out"><Checkbox checked={f.enabled} aria-label={`Pull out ${f.label}`} onChange={(_, d) => setField(f.key, { enabled: d.checked, required: d.checked && f.required })} /></TableCell>
                      <TableCell data-label="Field">{f.label}</TableCell>
                      <TableCell data-label="Type">{f.type}</TableCell>
                      <TableCell data-label="Required"><Switch checked={f.required} disabled={!f.enabled} aria-label={`${f.label} required`} onChange={(_, d) => setField(f.key, { required: d.checked })} /></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          ))}
        </div>
      )}

      {tab === 'reading' && (
        <div role="tabpanel" aria-label="Reading" className="form-grid">
          <Field label="Quality">
            <RadioGroup value={p.preset} onChange={(_, d) => set({ preset: d.value })}>
              <Radio value="fast" label="Fast" />
              <Radio value="balanced" label="Balanced" />
              <Radio value="accurate" label="Most accurate" />
            </RadioGroup>
          </Field>
          <Field label="Where the AI runs">
            <RadioGroup value={p.onPremOnly ? 'local' : p.aiSource} onChange={(_, d) => set({ aiSource: d.value })}>
              <Radio value="local" label="Our servers" />
              <Radio value="cloud" label="Cloud AI service" disabled={p.onPremOnly} />
            </RadioGroup>
          </Field>
          <Field label="Data stays on-premises" hint="Cloud services are never used for this profile, even as a fallback">
            <Switch checked={p.onPremOnly} label={p.onPremOnly ? 'On' : 'Off'} onChange={(_, d) => set({ onPremOnly: d.checked, aiSource: d.checked ? 'local' : p.aiSource })} />
          </Field>
          <Field label="Document languages">
            <Dropdown multiselect value={p.languages.join(', ')} selectedOptions={p.languages} onOptionSelect={(_, d) => set({ languages: d.selectedOptions })}>
              {LANGS.map((l) => <Option key={l}>{l}</Option>)}
            </Dropdown>
          </Field>
          <Field label="Accuracy check" hint="A second AI model reviews every value the first one found">
            <Switch checked={p.evaluator} label={p.evaluator ? 'On' : 'Off'} onChange={(_, d) => set({ evaluator: d.checked })} />
          </Field>
          <div className="span-2"><Link onClick={() => go('engines')}>Open AI and engine settings</Link></div>
        </div>
      )}

      {tab === 'output' && (
        <div role="tabpanel" aria-label="Where results go" className="stack">
          <Field label="Export as">
            {Object.entries(EXPORTS).map(([k, v]) => (
              <Checkbox key={k} label={v} checked={p.exports.includes(k)} onChange={(_, d) => set({ exports: d.checked ? [...p.exports, k] : p.exports.filter((e) => e !== k) })} />
            ))}
          </Field>
          {p.exports.includes('database') && (
            <Field label="Database tables" hint="People can see these tables in Databases" className="narrow-field">
              <Dropdown multiselect placeholder="Choose tables" value={p.linked.join(', ')} selectedOptions={p.linked} onOptionSelect={(_, d) => set({ linked: d.selectedOptions })}>
                {CONNECTIONS.flatMap((c) => Object.keys(tables[c.id] || {}).map((t) => <Option key={`${c.id}.${t}`} value={`${c.id}.${t}`}>{`${c.name}, ${t}`}</Option>))}
              </Dropdown>
            </Field>
          )}
          <Title as="h2" size="section">Automations</Title>
          <div className="table-card">
            <Table aria-label="Automations for this profile" className="responsive">
              <TableHeader><TableRow><TableHeaderCell>Automation</TableHeaderCell><TableHeaderCell>When</TableHeaderCell><TableHeaderCell>Then</TableHeaderCell><TableHeaderCell>On for this profile</TableHeaderCell></TableRow></TableHeader>
              <TableBody>
                {workflows.map((w) => (
                  <TableRow key={w.id}>
                    <TableCell data-label="Automation">{w.name}</TableCell>
                    <TableCell data-label="When">{WHEN[w.when]}</TableCell>
                    <TableCell data-label="Then">{ACTION[w.action]}</TableCell>
                    <TableCell data-label="On for this profile">
                      <Switch aria-label={`${w.name} for ${p.name}`} checked={w.profile === p.id || w.profile === 'all'}
                        onChange={(_, d) => { setWorkflows(workflows.map((x) => (x.id === w.id ? { ...x, profile: d.checked ? p.id : '' } : x))); notify(`${w.name} turned ${d.checked ? 'on' : 'off'} for ${p.name}`, 'info'); }} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      <Dialog open={adding} onOpenChange={(_, d) => setAdding(d.open)}>
        <DialogSurface>
          <form onSubmit={(e) => {
            e.preventDefault();
            if (!newField.label.trim()) return;
            set({ fields: [...p.fields, { key: `custom_${Date.now()}`, label: newField.label.trim(), type: newField.type, group: 'Custom', enabled: true, required: false }] });
            setNewField({ label: '', type: 'Text' });
            setAdding(false);
          }}>
            <DialogBody>
              <DialogTitle>Add a field</DialogTitle>
              <DialogContent className="form">
                <Field label="Field name" required><Input value={newField.label} onChange={(_, d) => setNewField({ ...newField, label: d.value })} placeholder="For example, Port of loading" /></Field>
                <Field label="Type">
                  <Dropdown value={newField.type} selectedOptions={[newField.type]} onOptionSelect={(_, d) => setNewField({ ...newField, type: d.optionValue })}>
                    {['Text', 'Number', 'Amount', 'Date'].map((t) => <Option key={t}>{t}</Option>)}
                  </Dropdown>
                </Field>
              </DialogContent>
              <DialogActions>
                <Button appearance="primary" type="submit" disabled={!newField.label.trim()}>Add</Button>
                <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
              </DialogActions>
            </DialogBody>
          </form>
        </DialogSurface>
      </Dialog>

      <Dialog modalType="alert" open={removing} onOpenChange={(_, d) => setRemoving(d.open)}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>{`Delete ${p.name}?`}</DialogTitle>
            <DialogContent>New uploads can't use it anymore. Documents already read keep their values.</DialogContent>
            <DialogActions>
              <Button appearance="primary" onClick={() => { setProfiles(profiles.filter((x) => x.id !== p.id)); notify(`${p.name} deleted`); go('profiles'); }}>Delete</Button>
              <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
            </DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </div>
  );
}
