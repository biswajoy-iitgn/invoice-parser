import { useState } from 'react';
import {
  Button, Card, CardHeader, CardFooter, Switch, Field, Input, Dropdown, Option, Text, Caption1,
  RadioGroup, Radio, Textarea, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
} from '@fluentui/react-components';
import { AddRegular, PlayRegular, EditRegular, DeleteRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, EmptyState, Picto } from '../ui';
import { WHEN, ACTION } from '../data';

// Lower only the first letter, so names like Teams keep their capital.
const lower = (t) => t.charAt(0).toLowerCase() + t.slice(1);
const BLANK = { name: '', when: 'accepted', action: 'webhook', target: '', profile: 'all', enabled: true, last: '' };
const TARGET = { webhook: ['Address', 'https://'], notify: ['Send to', 'Teams channel or email address'], approval: ['Approver group', 'For example, Finance approvers'] };
const PICTO = { webhook: 'link', notify: 'envelope', approval: 'check_mark_button' };

export default function Workflows() {
  const { workflows, setWorkflows, profiles, notify } = useApp();
  const [edit, setEdit] = useState(null);
  const [removing, setRemoving] = useState(null);
  const pname = (id) => (id === 'all' ? 'all profiles' : profiles.find((p) => p.id === id)?.name || 'no profile');
  const save = () => {
    setWorkflows(edit.id ? workflows.map((w) => (w.id === edit.id ? edit : w)) : [...workflows, { ...edit, id: `wf-${Date.now()}` }]);
    notify(`${edit.name} saved`);
    setEdit(null);
  };
  const badUrl = edit?.action === 'webhook' && edit.target && !/^https:\/\//.test(edit.target);

  return (
    <div className="page">
      <PageHeader picto="link" title="Automations" description="Do something automatically when documents are read, like posting to ERP or asking someone to approve.">
        <Button appearance="primary" icon={<AddRegular />} onClick={() => setEdit(BLANK)}>New automation</Button>
      </PageHeader>
      {workflows.length === 0 ? (
        <EmptyState picto="link" title="No automations yet" body="Connect document results to your other systems.">
          <Button appearance="primary" onClick={() => setEdit(BLANK)}>New automation</Button>
        </EmptyState>
      ) : (
        <div className="grid-3">
          {workflows.map((w) => {
            return (
              <Card key={w.id} className="card">
                <CardHeader image={<Picto name={PICTO[w.action]} size={44} />} header={<Text as="h2" weight="semibold" size={400}>{w.name}</Text>}
                  description={<Caption1>{w.last ? `Last ran ${w.last}` : 'Hasn\'t run yet'}</Caption1>}
                  action={<Switch aria-label={`${w.name} on`} checked={w.enabled} onChange={(_, d) => { setWorkflows(workflows.map((x) => (x.id === w.id ? { ...x, enabled: d.checked } : x))); notify(`${w.name} turned ${d.checked ? 'on' : 'off'}`, 'info'); }} />} />
                <Text>{`When ${lower(WHEN[w.when])} in ${pname(w.profile)}, ${lower(ACTION[w.action])}.`}</Text>
                <Caption1 className="muted ellipsis">{w.target}</Caption1>
                <CardFooter>
                  <Button icon={<PlayRegular />} disabled={!w.enabled} onClick={() => { setWorkflows(workflows.map((x) => (x.id === w.id ? { ...x, last: 'Just now' } : x))); notify(`${w.name} ran`, 'success', 'Used the latest accepted document'); }}>Run now</Button>
                  <Button appearance="subtle" icon={<EditRegular />} onClick={() => setEdit(w)}>Edit</Button>
                  <Button appearance="subtle" icon={<DeleteRegular />} aria-label={`Delete ${w.name}`} onClick={() => setRemoving(w)} />
                </CardFooter>
              </Card>
            );
          })}
        </div>
      )}

      <Dialog open={!!edit} onOpenChange={(_, d) => !d.open && setEdit(null)}>
        <DialogSurface>
          {edit && (
            <form onSubmit={(e) => { e.preventDefault(); if (edit.name.trim() && edit.target.trim() && !badUrl) save(); }}>
              <DialogBody>
                <DialogTitle>{edit.id ? 'Edit automation' : 'New automation'}</DialogTitle>
                <DialogContent className="form">
                  <Field label="Name" required><Input value={edit.name} onChange={(_, d) => setEdit({ ...edit, name: d.value })} /></Field>
                  <Field label="When">
                    <Dropdown value={WHEN[edit.when]} selectedOptions={[edit.when]} onOptionSelect={(_, d) => setEdit({ ...edit, when: d.optionValue })}>
                      {Object.entries(WHEN).map(([k, v]) => <Option key={k} value={k}>{v}</Option>)}
                    </Dropdown>
                  </Field>
                  <Field label="For">
                    <Dropdown value={edit.profile === 'all' ? 'All profiles' : pname(edit.profile)} selectedOptions={[edit.profile]} onOptionSelect={(_, d) => setEdit({ ...edit, profile: d.optionValue })}>
                      <Option value="all">All profiles</Option>
                      {profiles.map((p) => <Option key={p.id} value={p.id}>{p.name}</Option>)}
                    </Dropdown>
                  </Field>
                  <Field label="Then">
                    <RadioGroup value={edit.action} onChange={(_, d) => setEdit({ ...edit, action: d.value })}>
                      {Object.entries(ACTION).map(([k, v]) => <Radio key={k} value={k} label={v} />)}
                    </RadioGroup>
                  </Field>
                  <Field label={TARGET[edit.action][0]} required validationState={badUrl ? 'error' : 'none'} validationMessage={badUrl ? 'Use an address that starts with https://' : undefined}>
                    <Input value={edit.target} placeholder={TARGET[edit.action][1]} onChange={(_, d) => setEdit({ ...edit, target: d.value })} />
                  </Field>
                  {edit.action === 'webhook' && (
                    <Field label="Message body" hint="Use {{field_name}} to insert a value">
                      <Textarea resize="vertical" defaultValue={'{\n  "document": "{{file_name}}",\n  "fields": {{fields_json}}\n}'} textarea={{ style: { fontFamily: 'monospace' } }} />
                    </Field>
                  )}
                </DialogContent>
                <DialogActions>
                  <Button appearance="primary" type="submit" disabled={!edit.name.trim() || !edit.target.trim() || badUrl}>Save</Button>
                  <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
                </DialogActions>
              </DialogBody>
            </form>
          )}
        </DialogSurface>
      </Dialog>

      <Dialog modalType="alert" open={!!removing} onOpenChange={(_, d) => !d.open && setRemoving(null)}>
        <DialogSurface>
          {removing && (
            <DialogBody>
              <DialogTitle>{`Delete ${removing.name}?`}</DialogTitle>
              <DialogContent>It stops running right away. Past runs stay in the audit log.</DialogContent>
              <DialogActions>
                <Button appearance="primary" onClick={() => { setWorkflows(workflows.filter((x) => x.id !== removing.id)); notify(`${removing.name} deleted`); setRemoving(null); }}>Delete</Button>
                <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
              </DialogActions>
            </DialogBody>
          )}
        </DialogSurface>
      </Dialog>
    </div>
  );
}
