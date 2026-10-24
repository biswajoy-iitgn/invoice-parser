import { useRef, useState } from 'react';
import {
  Button, Text, Card, CardPreview, CardHeader, Caption1, Body1, ProgressBar, MessageBar, MessageBarBody, MessageBarTitle, MessageBarActions,
  Menu, MenuTrigger, MenuPopover, MenuList, MenuItem, MenuButton, Dropdown, Option, Link, CompoundButton, Field, Avatar,
  Table, TableHeader, TableRow, TableHeaderCell, TableBody, TableCell,
} from '@fluentui/react-components';
import { ArrowUploadRegular, DocumentRegular, FolderRegular, DocumentPdfRegular, ImageRegular, DocumentOnePageSparkleRegular } from '@fluentui/react-icons';
import { useApp, EmptyState, StatusBadge, Title, Picto } from '../ui';
import { DOCS, FLAG_BELOW, CONNECTIONS } from '../data';

export function useUpload(profileId) {
  const { startRun } = useApp();
  const files = useRef(null);
  const folder = useRef(null);
  const forced = profileId === 'auto' ? null : profileId;
  const start = (list, source) => {
    const items = list.filter((f) => !f.name.startsWith('.')).map((f) => ({ name: f.name, file: f }));
    if (items.length) startRun(items, forced, source || (items.length > 1 ? `${items.length} files` : items[0].name));
  };
  const inputs = (
    <>
      <input ref={files} type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff" hidden onChange={(e) => { start([...e.target.files]); e.target.value = ''; }} />
      <input ref={folder} type="file" webkitdirectory="" hidden onChange={(e) => {
        const list = [...e.target.files];
        start(list, list[0] ? `Folder: ${list[0].webkitRelativePath.split('/')[0]}` : '');
        e.target.value = '';
      }} />
    </>
  );
  const samples = () => startRun(Object.values(DOCS).filter((d) => d.kind === 'vector').map((d) => ({ name: d.name, sampleId: d.id })), forced, 'Sample invoices');
  return { inputs, openFiles: () => files.current.click(), openFolder: () => folder.current.click(), drop: start, samples };
}

// Command bar upload: one menu, like New/Upload in OneDrive.
export function UploadMenu({ profileId = 'auto', primary = true }) {
  const up = useUpload(profileId);
  return (
    <>
      <Menu>
        <MenuTrigger disableButtonEnhancement>
          <MenuButton appearance={primary ? 'primary' : 'subtle'} icon={<ArrowUploadRegular />}>Upload</MenuButton>
        </MenuTrigger>
        <MenuPopover>
          <MenuList>
            <MenuItem icon={<DocumentRegular />} onClick={up.openFiles}>Files</MenuItem>
            <MenuItem icon={<FolderRegular />} onClick={up.openFolder}>Folder</MenuItem>
            <MenuItem icon={<DocumentOnePageSparkleRegular />} onClick={up.samples}>Sample invoices</MenuItem>
          </MenuList>
        </MenuPopover>
      </Menu>
      {up.inputs}
    </>
  );
}

export const FileIcon = ({ kind }) => (kind === 'vector' ? <DocumentPdfRegular className="ft-pdf" fontSize={20} aria-hidden /> : <ImageRegular className="ft-img" fontSize={20} aria-hidden />);

// The whole card is the action (Fluent card guidance: one obvious action).
export const cardAction = (fn, label) => ({
  role: 'button', tabIndex: 0, 'aria-label': label, onClick: fn,
  onKeyDown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } },
});

const greeting = () => { const h = new Date().getHours(); return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'; };

export default function Home() {
  const { profiles, allDocs, runs, go, workflows, role } = useApp();
  const [profileId, setProfileId] = useState('auto');
  const [over, setOver] = useState(false);
  const up = useUpload(profileId);
  const running = runs.find((r) => r.status === 'running');
  const queue = allDocs.filter((d) => d.status === 'review');
  const recent = allDocs.filter((d) => !['queued', 'running'].includes(d.status)).slice(0, 5);
  const online = CONNECTIONS.filter((c) => c.status === 'connected').length;
  const on = workflows.filter((w) => w.enabled).length;
  // Every capability one click from Home: databases, automations, engines, insights.
  const workspace = [
    ['data', 'card_index_dividers', 'Databases', `${online} connected, ${CONNECTIONS.length - online} offline`],
    role === 'admin' && ['workflows', 'link', 'Automations', `${on} on, ${workflows.length - on} off`],
    role === 'admin' && ['engines', 'gear', 'AI and engines', '7 of 8 engines online'],
    ['insights', 'chart_increasing', 'Insights', 'Spend, accuracy, and trends'],
  ].filter(Boolean);

  return (
    <div
      className={`page home${over ? ' is-over' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setOver(false); }}
      onDrop={(e) => { e.preventDefault(); setOver(false); up.drop([...e.dataTransfer.files]); }}
    >
      <section className="welcome glow-sky-growth" aria-labelledby="hello">
        <div className="welcome-copy">
          <Title id="hello" size="hello">{`${greeting()}, Biswajoy`}</Title>
          <Body1 className="welcome-lead">
            {queue.length ? `${queue.length} documents are waiting for a quick check. The rest are already done.` : 'Everything is checked. Drop new files whenever you are ready.'}
          </Body1>
          <div className="welcome-actions">
            <CompoundButton className="tile" icon={<Picto name="page_facing_up" size={40} />} secondaryContent="PDFs, scans, or photos" onClick={up.openFiles} data-opens="file-picker">Upload files</CompoundButton>
            <CompoundButton className="tile" icon={<Picto name="open_file_folder" size={40} />} secondaryContent="One batch per folder" onClick={up.openFolder} data-opens="file-picker">Upload a folder</CompoundButton>
            <CompoundButton className="tile" icon={<Picto name="sparkles" size={40} />} secondaryContent="7 real freight invoices" onClick={up.samples}>Try the samples</CompoundButton>
          </div>
          <Field label="Read files as" orientation="horizontal" className="welcome-profile">
            <Dropdown appearance="filled-lighter" value={profileId === 'auto' ? 'Auto-detect' : profiles.find((p) => p.id === profileId).name}
              selectedOptions={[profileId]} onOptionSelect={(_, d) => setProfileId(d.optionValue)}>
              <Option value="auto" text="Auto-detect">Auto-detect</Option>
              {profiles.map((p) => <Option key={p.id} value={p.id} text={p.name}>{p.name}</Option>)}
            </Dropdown>
          </Field>
        </div>
        <img src="/pictograms/inbox_tray.png" alt="" className="welcome-art float" draggable={false} />
        {up.inputs}
      </section>

      {over && <div className="drop-hint" aria-hidden>Drop to upload</div>}

      {running && (
        <MessageBar intent="info" layout="multiline">
          <MessageBarBody>
            <MessageBarTitle>{`Reading ${running.docs.length} documents …`}</MessageBarTitle>
            <ProgressBar value={running.docs.filter((d) => ['accepted', 'review', 'failed'].includes(d.status)).length / running.docs.length} aria-label="Batch progress" />
            <Caption1>Usually under a minute. You can keep working.</Caption1>
          </MessageBarBody>
          <MessageBarActions><Button onClick={() => go('run', running.id)}>View</Button></MessageBarActions>
        </MessageBar>
      )}

      <section aria-labelledby="foryou-h" className="section">
        <div className="section-head">
          <Title as="h2" size="section" id="foryou-h">Waiting for you</Title>
          {queue.length > 4 && <Link onClick={() => go('review')}>{`See all ${queue.length}`}</Link>}
        </div>
        {queue.length === 0 ? (
          <div className="glow-growth rounded"><EmptyState compact picto="check_mark_button" title="You're all caught up" body="New documents that need a look will show up here." /></div>
        ) : (
          <div className="file-cards">
            {queue.slice(0, 4).map((d) => {
              const sample = DOCS[d.sample || d.id];
              const flags = sample.fields.filter((f) => f.conf < FLAG_BELOW).length;
              return (
                <Card key={d.id} className="file-card lift" {...cardAction(() => go('review', d.id), `Review ${d.name}, ${flags} to check`)}>
                  <CardPreview className="file-card-preview"><img src={sample.pages[0].src} alt="" /></CardPreview>
                  <CardHeader image={<FileIcon kind={d.kind} />} header={<Text weight="semibold" truncate wrap={false}>{d.name}</Text>}
                    description={<Caption1>{`${flags} ${flags === 1 ? 'value' : 'values'} to check`}</Caption1>} />
                </Card>
              );
            })}
          </div>
        )}
      </section>

      <section aria-labelledby="ws-h" className="section">
        <div className="section-head"><Title as="h2" size="section" id="ws-h">Your workspace</Title></div>
        <div className="workspace">
          {workspace.map(([key, picto, name, status]) => (
            <Card key={key} orientation="horizontal" className="ws-card lift" {...cardAction(() => go(key), `${name}, ${status}`)}>
              <Picto name={picto} size={40} />
              <div><Text weight="semibold" block>{name}</Text><Caption1 className="muted">{status}</Caption1></div>
            </Card>
          ))}
        </div>
      </section>

      <section aria-labelledby="recent-h" className="section">
        <div className="section-head">
          <Title as="h2" size="section" id="recent-h">Recent</Title>
          <Link onClick={() => go('library')}>All documents</Link>
        </div>
        {recent.length === 0 ? (
          <EmptyState picto="inbox_tray" title="No documents yet" body="Upload files or a folder, or drop them anywhere on this page.">
            <Button appearance="primary" onClick={up.openFiles} data-opens="file-picker">Upload files</Button>
          </EmptyState>
        ) : (
          <Card className="list-card">
            <Table aria-label="Recent documents" className="responsive list-table">
              <TableHeader><TableRow><TableHeaderCell>Name</TableHeaderCell><TableHeaderCell>Vendor</TableHeaderCell><TableHeaderCell>Uploaded by</TableHeaderCell><TableHeaderCell>Status</TableHeaderCell></TableRow></TableHeader>
              <TableBody>
                {recent.map((d) => {
                  const sample = DOCS[d.sample || d.id];
                  const run = runs.find((r) => r.id === d.run);
                  return (
                    <TableRow key={d.id}>
                      <TableCell data-label="Name"><div className="name-cell"><FileIcon kind={d.kind} /><Link appearance="subtle" onClick={() => go('review', d.id)}>{d.name}</Link></div></TableCell>
                      <TableCell data-label="Vendor">{sample?.vendor || ''}</TableCell>
                      <TableCell data-label="Uploaded by"><div className="name-cell"><Avatar name={run?.by} size={20} color="colorful" /><Caption1>{`${run?.by?.split(' ')[0]}, ${run?.started}`}</Caption1></div></TableCell>
                      <TableCell data-label="Status"><StatusBadge status={d.status} /></TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Card>
        )}
      </section>
    </div>
  );
}
