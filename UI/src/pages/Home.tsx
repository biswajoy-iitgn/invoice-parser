import { lazy, Suspense, useRef, useState } from 'react';
import type { InputHTMLAttributes, KeyboardEvent } from 'react';
import {
  Button, Text, Card, CardPreview, CardHeader, Caption1, ProgressBar, MessageBar, MessageBarBody, MessageBarTitle, MessageBarActions,
  Menu, MenuTrigger, MenuPopover, MenuList, MenuItem, MenuButton, Link, CompoundButton, Avatar, Tooltip,
} from '@fluentui/react-components';
import { ArrowUploadRegular, DocumentRegular, FolderRegular, DocumentPdfRegular, ImageRegular, PauseRegular, PlayRegular, DocumentTextExtractRegular, DocumentSearchRegular } from '@fluentui/react-icons';
import { useCardStyles } from '../styles';
import { useApp, EmptyState, StatusBadge, Title, Glyph, RHead, RHeader, RBody, RRow, RCell, RTable, CellLink, count, useMedia } from '../ui';
import { DOCS } from '../data';
import type { DocKind } from '../types';

const RemoveSamples = lazy(() => import('../dialogs').then((m) => ({ default: m.RemoveSamplesDialog })));

// Chrome, Edge, Safari and Firefox all support folder picking through this non-standard attribute.
const FOLDER_PICKER = { webkitdirectory: '' } as InputHTMLAttributes<HTMLInputElement>;

const READABLE = /\.(pdf|png|jpe?g|tiff?|heic)$/i;
/** Files from a drop, opening dropped folders (and the folders inside them). */
async function dropped(dt: DataTransfer): Promise<{ files: File[]; folder?: string }> {
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter((e): e is FileSystemEntry => !!e); // read during the drop event
  if (!entries.length) return { files: [...dt.files] };
  const files: File[] = [];
  const walk = async (e: FileSystemEntry): Promise<void> => {
    if (e.isFile) { files.push(await new Promise<File>((ok, fail) => (e as FileSystemFileEntry).file(ok, fail))); return; }
    const reader = (e as FileSystemDirectoryEntry).createReader();
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((ok, fail) => reader.readEntries(ok, fail)); // a page at a time
      if (!batch.length) return;
      for (const x of batch) await walk(x);
    }
  };
  for (const e of entries) await walk(e);
  return { files, folder: entries.length === 1 && entries[0].isDirectory ? entries[0].name : undefined };
}

export function useUpload(profileId: string) {
  const { startRun, notify } = useApp();
  const files = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const forced = profileId === 'auto' ? null : profileId;
  const start = (list: File[], source?: string) => {
    const real = list.filter((f) => !f.name.startsWith('.'));
    const items = real.filter((f) => READABLE.test(f.name)).map((f) => ({ name: f.name, file: f }));
    const skipped = real.length - items.length;
    if (skipped) notify(`${skipped} ${skipped === 1 ? "file wasn't" : "files weren't"} read`, 'warning', 'Only PDF, JPG, PNG, TIFF and HEIC files are read.');
    if (items.length) startRun(items, forced, source || (items.length > 1 ? `${items.length} files` : items[0].name));
  };
  const inputs = (
    <>
      <input ref={files} type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.heic" hidden onChange={(e) => { start([...(e.target.files ?? [])]); e.target.value = ''; }} />
      <input ref={camera} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { start([...(e.target.files ?? [])], 'Phone camera'); e.target.value = ''; }} />
      <input ref={folder} type="file" {...FOLDER_PICKER} hidden onChange={(e) => {
        const list = [...(e.target.files ?? [])];
        start(list, list[0] ? `Folder: ${list[0].webkitRelativePath.split('/')[0]}` : '');
        e.target.value = '';
      }} />
    </>
  );
  return {
    inputs, openFiles: () => files.current?.click(), openFolder: () => folder.current?.click(), openCamera: () => camera.current?.click(),
    drop: (dt: DataTransfer) => { dropped(dt).then(({ files: list, folder: name }) => start(list, name && `Folder: ${name}`)); },
  };
}

// Command bar upload: one menu, like New/Upload in OneDrive.
export function UploadMenu({ profileId = 'auto', primary = true }: { profileId?: string; primary?: boolean }) {
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
          </MenuList>
        </MenuPopover>
      </Menu>
      {up.inputs}
    </>
  );
}

export const FileIcon = ({ kind }: { kind: DocKind }) => (kind === 'vector' ? <DocumentPdfRegular className="ft-pdf" fontSize={20} aria-hidden /> : <ImageRegular className="ft-img" fontSize={20} aria-hidden />);

// The whole card is the action (Fluent card guidance: one obvious action).
export const cardAction = (fn: () => void, label: string) => ({
  role: 'button', tabIndex: 0, 'aria-label': label, onClick: fn,
  onKeyDown: (e: KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } },
});

// WCAG 2.2.2: the hero glow moves for longer than 5 s, so it has a pause control; reduced motion starts it paused.
const stillAtStart = () => { try { return localStorage.getItem('hero-still') === '1' || matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; } };
const greeting = () => { const h = new Date().getHours(); return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'; };

export default function Home() {
  const { allDocs, runs, go, docRecord, me, flagBelow, samples } = useApp();
  const [removing, setRemoving] = useState(false);
  const narrow = useMedia('(max-width: 900px)'); // the bar's text and action don't fit one line
  const [still, setStill] = useState(stillAtStart);
  const cs = useCardStyles();
  const running = runs.find((r) => r.status === 'running');
  const queue = allDocs.filter((d) => d.status === 'review');
  const next = queue[0];
  const recent = allDocs.filter((d) => !['queued', 'running'].includes(d.status)).slice(0, 5);

  // Home is an overview: reading new files lives on Extract, checking them on Review.
  return (
    <div className="page home">
      <section className={`welcome${still ? ' is-still' : ''}`} aria-labelledby="hello">
        <div className="aurora" aria-hidden="true"><span /><span /><span /></div>
        <Tooltip content={still ? 'Play background animation' : 'Pause background animation'} relationship="label">
          <Button appearance="subtle" size="small" className="aurora-toggle" icon={still ? <PlayRegular /> : <PauseRegular />}
            onClick={() => { setStill(!still); try { localStorage.setItem('hero-still', still ? '0' : '1'); } catch { /* this visit only */ } }} />
        </Tooltip>
        <div className="welcome-copy">
          <Title id="hello" size="hello">{`${greeting()}, ${me.name.split(' ')[0]}`}</Title>
          <div className="welcome-actions">
            <CompoundButton className="tile" icon={<Glyph icon={DocumentTextExtractRegular} />} secondaryContent="Files or a whole folder" onClick={() => go('extract')}>Extract documents</CompoundButton>
            {next && (
              <CompoundButton className="tile" icon={<Glyph icon={DocumentSearchRegular} />} secondaryContent={`${queue.length} waiting, next is ${next.name}`} onClick={() => go('review', next.id)}>Review next</CompoundButton>
            )}
          </div>
        </div>
      </section>

      {samples && runs.length > 0 && (
        <MessageBar intent="info" layout={narrow ? 'multiline' : 'singleline'}>
          <MessageBarBody>This workspace has sample documents so you can try things out.</MessageBarBody>
          <MessageBarActions><Button onClick={() => setRemoving(true)}>Remove sample data</Button></MessageBarActions>
        </MessageBar>
      )}

      {running && (
        <MessageBar intent="info" layout="multiline">
          <MessageBarBody>
            <MessageBarTitle>{`Reading ${running.docs.length} documents\u00a0…`}</MessageBarTitle>
            <ProgressBar value={running.docs.filter((d) => ['accepted', 'review', 'failed'].includes(d.status)).length / running.docs.length} aria-label="Batch progress" />
            <Caption1>{`${running.docs.filter((d) => ['accepted', 'review', 'failed'].includes(d.status)).length} of ${running.docs.length} read. You can keep working.`}</Caption1>
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
              // a document the service read is not a bundled sample: no thumbnail, and its flagged values come with it
              const sample = DOCS[d.sample || d.id];
              const flags = sample ? sample.fields.filter((f) => f.conf < flagBelow).length : d.flagged?.length ?? 0;
              return (
                <Card key={d.id} className="file-card lift" {...cardAction(() => go('review', d.id), `Review ${d.name}, ${flags} to check`)}>
                  <CardPreview className="file-card-preview">{sample ? <img src={sample.pages[0].thumb || sample.pages[0].src} alt="" loading="lazy" decoding="async" /> : <div className="file-card-blank"><FileIcon kind={d.kind} /></div>}</CardPreview>
                  <CardHeader className={cs.fileHeader} image={<FileIcon kind={d.kind} />} header={<Text weight="semibold" truncate wrap={false}>{d.name}</Text>}
                    description={<Caption1>{`${count(flags, 'value', 'values')} to check`}</Caption1>} />
                </Card>
              );
            })}
          </div>
        )}
      </section>

      <section aria-labelledby="recent-h" className="section">
        <div className="section-head">
          <Title as="h2" size="section" id="recent-h">Recent</Title>
          <Link onClick={() => go('library')}>All documents</Link>
        </div>
        {recent.length === 0 ? (
          <EmptyState picto="inbox_tray" title="No documents yet" body="Read your first documents on Extract.">
            <Button appearance="primary" onClick={() => go('extract')}>Go to Extract</Button>
          </EmptyState>
        ) : (
          <Card className="list-card">
            <RTable aria-label="Recent documents" list>
              <RHeader><RRow><RHead>Name</RHead><RHead>Company</RHead><RHead>Uploaded by</RHead><RHead>Status</RHead></RRow></RHeader>
              <RBody>
                {recent.map((d) => {
                  const run = runs.find((r) => r.id === d.run);
                  return (
                    <RRow key={d.id}>
                      <RCell data-label="Name"><div className="name-cell"><FileIcon kind={d.kind} /><CellLink onClick={() => go('review', d.id)}>{d.name}</CellLink></div></RCell>
                      <RCell data-label="Company">{(({ vendor, supplier, consignee }) => vendor || supplier || consignee || '')(docRecord(d.id)?.values ?? {})}</RCell>
                      <RCell data-label="Uploaded by"><div className="name-cell"><Avatar name={run?.by} size={20} color="brand" /><Caption1>{`${run?.by?.split(' ')[0]}, ${run?.started}`}</Caption1></div></RCell>
                      <RCell data-label="Status"><StatusBadge status={d.status} /></RCell>
                    </RRow>
                  );
                })}
              </RBody>
            </RTable>
          </Card>
        )}
      </section>
      {removing && <Suspense fallback={null}><RemoveSamples close={() => setRemoving(false)} /></Suspense>}
    </div>
  );
}
