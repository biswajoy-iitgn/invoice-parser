import { useState } from 'react';
import {
  Button, Card, CardHeader, CardFooter, Switch, Field, Input, Dropdown, Option, Text, Caption1,
  RadioGroup, Radio, Textarea, Checkbox, Body1, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
} from '@fluentui/react-components';
import { PlugConnectedRegular, MailRegular, TaskListSquareLtrRegular, AddRegular, PlayRegular, EditRegular, DeleteRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, EmptyState, Glyph } from '../ui';
import { useCardStyles } from '../styles';
import { WHEN, ACTION } from '../data';
import type { Action, When, Workflow } from '../types';

type Draft = Omit<Workflow, 'id'> & { id?: string };

// Lower only the first letter, so names like Teams keep their capital.
const lower = (t: string) => t.charAt(0).toLowerCase() + t.slice(1);
const BODY = '{\n  "document": "{{file_name}}",\n  "fields": {{fields_json}}\n}';
const BLANK: Draft = { name: '', when: 'accepted', action: 'webhook', target: '', profiles: 'all', body: BODY, enabled: true, last: '' };
const TARGET: Record<Action, [string, string]> = { webhook: ['Address', 'https://'], notify: ['Send to', 'Teams channel or email address'], approval: ['Approver group', 'For example, Finance approvers'] };
const ICONS: Record<Action, typeof MailRegular> = { webhook: PlugConnectedRegular, notify: MailRegular, approval: TaskListSquareLtrRegular };

export default function Workflows() {
  const { workflows, setWorkflows, profiles, notify, log } = useApp();
  const cs = useCardStyles();
  const [edit, setEdit] = useState<Draft | null>(null);
  const [removing, setRemoving] = useState<Workflow | null>(null);
  const [running, setRunning] = useState<Workflow | null>(null);
  const names = (ps: Workflow['profiles']) => (ps === 'all' ? 'all profiles' : profiles.filter((p) => ps.includes(p.id)).map((p) => p.name).join(', ') || 'no profile yet');
  const save = () => {
    if (!edit) return;
    const { id } = edit;
    setWorkflows(id ? workflows.map((w) => (w.id === id ? { ...edit, id } : w)) : [...workflows, { ...edit, id: `wf-${Date.now()}` }]);
    log(id ? 'Changed automation' : 'Created automation', edit.name);
    notify(`${edit.name} saved`);
    setEdit(null);
  };
  const run = (w: Workflow) => {
    setWorkflows(workflows.map((x) => (x.id === w.id ? { ...x, last: 'Just now' } : x)));
    log('Ran automation', `${w.name}, ${w.target}`);
    notify(`${w.name} started`, 'success', 'With the latest approved document');
    setRunning(null);
  };
  const badUrl = edit?.action === 'webhook' && !!edit.target && !/^https:\/\//.test(edit.target);

  return (
    <div className="page">
      <PageHeader title="Automations" description="Do something automatically when documents are read, like posting to ERP or asking someone to approve.">
        <Button appearance="primary" icon={<AddRegular />} onClick={() => setEdit(BLANK)}>New automation</Button>
      </PageHeader>
      {workflows.length === 0 ? (
        <EmptyState picto="counterclockwise_arrows_button" title="No automations yet" body="Connect document results to your other systems.">
          <Button appearance="primary" onClick={() => setEdit(BLANK)}>New automation</Button>
        </EmptyState>
      ) : (
        <div className="grid-3">
          {workflows.map((w) => {
            return (
              <Card key={w.id} className="card">
                <CardHeader image={<Glyph icon={ICONS[w.action]} />} header={<Text as="h2" weight="semibold" size={400}>{w.name}</Text>}
                  description={<Caption1>{w.last ? `Last ran ${w.last}` : 'Hasn\'t run yet'}</Caption1>}
                  action={<Switch aria-label={`${w.name} on`} checked={w.enabled} onChange={(_, d) => { setWorkflows(workflows.map((x) => (x.id === w.id ? { ...x, enabled: d.checked } : x))); log(`Turned automation ${d.checked ? 'on' : 'off'}`, w.name); notify(`${w.name} turned ${d.checked ? 'on' : 'off'}`, 'info'); }} />} />
                <Text>{`When ${lower(WHEN[w.when])} in ${names(w.profiles)}, ${lower(ACTION[w.action])}.`}</Text>
                <Caption1 className="muted ellipsis">{w.target}</Caption1>
                <CardFooter className={cs.footer}>
                  <Button icon={<PlayRegular />} disabled={!w.enabled} onClick={() => setRunning(w)}>Run now</Button>
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
                    <Dropdown value={WHEN[edit.when]} selectedOptions={[edit.when]} onOptionSelect={(_, d) => setEdit({ ...edit, when: d.optionValue as When })}>
                      {Object.entries(WHEN).map(([k, v]) => <Option key={k} value={k}>{v}</Option>)}
                    </Dropdown>
                  </Field>
                  <Field label="For">
                    <Checkbox label="All profiles, including new ones" checked={edit.profiles === 'all'} onChange={(_, d) => setEdit({ ...edit, profiles: d.checked ? 'all' : [] })} />
                    {edit.profiles !== 'all' && (
                      <Dropdown multiselect aria-label="Profiles" placeholder="Choose profiles" value={names(edit.profiles)} selectedOptions={edit.profiles}
                        onOptionSelect={(_, d) => setEdit({ ...edit, profiles: d.selectedOptions })}>
                        {profiles.map((p) => <Option key={p.id} value={p.id}>{p.name}</Option>)}
                      </Dropdown>
                    )}
                  </Field>
                  <Field label="Then">
                    <RadioGroup value={edit.action} onChange={(_, d) => setEdit({ ...edit, action: d.value as Action })}>
                      {Object.entries(ACTION).map(([k, v]) => <Radio key={k} value={k} label={v} />)}
                    </RadioGroup>
                  </Field>
                  <Field label={TARGET[edit.action][0]} required validationState={badUrl ? 'error' : 'none'} validationMessage={badUrl ? 'Use an address that starts with https://' : undefined}>
                    <Input value={edit.target} placeholder={TARGET[edit.action][1]} onChange={(_, d) => setEdit({ ...edit, target: d.value })} />
                  </Field>
                  {edit.action === 'webhook' && (
                    <Field label="Message body" hint="Use {{field_name}} to insert a value">
                      <Textarea resize="vertical" rows={5} value={edit.body ?? BODY} onChange={(_, d) => setEdit({ ...edit, body: d.value })} textarea={{ style: { fontFamily: 'monospace' } }} />
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

      <Dialog modalType="alert" open={!!running} onOpenChange={(_, d) => !d.open && setRunning(null)}>
        <DialogSurface>
          {running && (
            <DialogBody>
              <DialogTitle>{`Run ${running.name} now?`}</DialogTitle>
              <DialogContent><Body1>{`It ${lower(ACTION[running.action])} with the latest approved document. Destination: ${running.target}`}</Body1></DialogContent>
              <DialogActions>
                <Button appearance="primary" onClick={() => run(running)}>Run</Button>
                <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
              </DialogActions>
            </DialogBody>
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
                <Button appearance="primary" onClick={() => { setWorkflows(workflows.filter((x) => x.id !== removing.id)); log('Deleted automation', removing.name); notify(`${removing.name} deleted`); setRemoving(null); }}>Delete</Button>
                <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
              </DialogActions>
            </DialogBody>
          )}
        </DialogSurface>
      </Dialog>
    </div>
  );
}
