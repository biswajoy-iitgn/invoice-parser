import { useState } from 'react';
import {
  Button, Card, Caption1, Text, Field, Dropdown, Option, ProgressBar, Link,
  Avatar,
} from '@fluentui/react-components';
import { ArrowUploadRegular, DocumentRegular, FolderRegular, CameraRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, Title, EmptyState, RHead, RHeader, RBody, RRow, RCell, RTable, CellLink, useMedia } from '../ui';
import { usePaging } from '../sheet';
import { useUpload } from './Home';
import { profileName } from './Runs';
import type { DocStatus, Run } from '../types';

// Extract: bring files in. One drop target, the document type, what is reading now, and the batches already read.
export default function Extract() {
  const { profiles, runs, go } = useApp();
  const [profileId, setProfileId] = useState('auto');
  const [over, setOver] = useState(false);
  const up = useUpload(profileId);
  const touch = useMedia('(pointer: coarse)'); // phones and tablets: no drag and drop or folder picking, but a camera
  const running = runs.filter((r) => r.status === 'running');
  const done = runs.filter((r) => r.status !== 'running');
  const paged = usePaging(done, 'batches', 'batches');
  const count = (r: Run, ...s: DocStatus[]) => r.docs.filter((d) => s.includes(d.status)).length;
  const result = (r: Run) => [
    `${count(r, 'accepted', 'exported')} approved`,
    count(r, 'review') && `${count(r, 'review')} to review`,
    count(r, 'failed') && `${count(r, 'failed')} failed`,
  ].filter(Boolean).join(', ');

  return (
    <div className="page">
      <PageHeader title="Extract" description="Read values from any document a profile describes: invoices, orders, contracts, letters, datasheets. Anything the AI isn't sure about waits in Review." />

      <Card
        className={`dropzone${over ? ' is-over' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false); }}
        onDrop={(e) => { e.preventDefault(); setOver(false); up.drop(e.dataTransfer); }}
      >
        <span className="glyph glyph-l"><ArrowUploadRegular /></span>
        <Title as="h2" size="section">{over ? 'Drop to start reading' : touch ? 'Choose files or take a photo' : 'Drop files or a folder here'}</Title>
        <Caption1 className="muted">{touch ? 'PDF, JPG, PNG, TIFF or HEIC.' : 'PDF, JPG, PNG, TIFF or HEIC. A folder becomes one batch.'}</Caption1>
        <div className="row center">
          <Button appearance="primary" icon={<DocumentRegular />} onClick={up.openFiles} data-opens="file-picker">Choose files</Button>
          {touch
            ? <Button icon={<CameraRegular />} onClick={up.openCamera} data-opens="file-picker">Take a photo</Button>
            : <Button icon={<FolderRegular />} onClick={up.openFolder} data-opens="file-picker">Choose a folder</Button>}
        </div>
        <Field label="Document type" className="dropzone-type" hint={profileId === 'auto' ? 'Each file gets the profile that fits it best' : undefined}>
          <Dropdown value={profileId === 'auto' ? 'Auto-detect' : profiles.find((p) => p.id === profileId)?.name}
            selectedOptions={[profileId]} onOptionSelect={(_, d) => setProfileId(d.optionValue ?? 'auto')}>
            <Option value="auto" text="Auto-detect">Auto-detect</Option>
            {profiles.map((p) => <Option key={p.id} value={p.id} text={p.name}>{p.name}</Option>)}
          </Dropdown>
        </Field>
        {up.inputs}
      </Card>

      {running.length > 0 && (
        <section className="section" aria-labelledby="now-h">
          <Title as="h2" size="section" id="now-h">Reading now</Title>
          {running.map((r) => {
            const finished = count(r, 'accepted', 'exported', 'review', 'failed');
            return (
              <Card key={r.id} className="card">
                <Field label={<><Link onClick={() => go('run', r.id)}>{r.source}</Link>{` – ${finished} of ${r.docs.length} documents read`}</>} validationState="none">
                  <ProgressBar value={finished / r.docs.length} thickness="large" />
                </Field>
              </Card>
            );
          })}
        </section>
      )}

      <section className="section" aria-labelledby="batches-h">
        <Title as="h2" size="section" id="batches-h">Batches</Title>
        {done.length === 0 ? (
          <EmptyState compact picto="inbox_tray" title="No batches yet" body="Files you read show up here, one batch per upload." />
        ) : (
          <Card className="list-card">
            <RTable aria-label="Batches" list>
              <RHeader>
                <RRow>
                  <RHead>Batch</RHead><RHead>Document type</RHead><RHead>Uploaded by</RHead>
                  <RHead>Documents</RHead><RHead>Result</RHead>
                </RRow>
              </RHeader>
              <RBody>
                {paged.items.map((r) => (
                  <RRow key={r.id}>
                    <RCell data-label="Batch">
                      <div className="cell-stack"><CellLink onClick={() => go('run', r.id)}>{r.source.replace(/^Folder: /, '')}</CellLink><Caption1 className="muted">{`${r.id}, ${r.started}`}</Caption1></div>
                    </RCell>
                    <RCell data-label="Document type">{profileName(profiles, r.profile)}</RCell>
                    <RCell data-label="Uploaded by"><div className="name-cell"><Avatar name={r.by} size={20} color="brand" /><Text>{r.by}</Text></div></RCell>
                    <RCell data-label="Documents">{r.docs.length}</RCell>
                    <RCell data-label="Result">{result(r)}</RCell>
                  </RRow>
                ))}
              </RBody>
            </RTable>
          </Card>
        )}
        {paged.pager}
      </section>
    </div>
  );
}
