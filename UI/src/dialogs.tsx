// Dialogs opened from Help and Home, loaded on first use.
import { useState } from 'react';
import {
  Button, Text, Body1, Caption1, Field, Textarea, Checkbox, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger,
} from '@fluentui/react-components';
import { useApp, RHead, RHeader, RBody, RRow, RCell, RTable } from './ui';
import { UPDATES } from './data';
import type { HelpDialog } from './help';

const SHORTCUTS: [string, string, string][] = [
  ['Anywhere', 'Alt+Q', 'Search documents'],
  ['Anywhere', 'Shift+?', 'Show keyboard shortcuts'],
  ['Sheets', 'Arrow keys', 'Move between cells'],
  ['Sheets', 'Home, End', 'First or last cell in the row'],
  ['Sheets', 'Ctrl+Home, Ctrl+End', 'First or last cell in the sheet'],
  ['Sheets', 'Page Up, Page Down', 'Up or down a screen'],
  ['Sheets', 'Enter or F2', 'Edit the cell, or open the document'],
  ['Sheets', 'Type', 'Replace the cell'],
  ['Sheets', 'Delete', 'Clear the cell'],
  ['Editing a cell', 'Enter, Tab', 'Save and move down or right'],
  ['Editing a cell', 'Escape', 'Cancel'],
  ['Review', 'Up, Down, Enter', 'Move through the queue and open a document'],
];
const STEPS: [string, string, string][] = [
  ['Read documents', 'Upload files or a folder on Extract. Each upload becomes a batch.', 'extract'],
  ['Check what the AI wasn’t sure about', 'Documents with doubtful values wait in Review. Check them, then approve.', 'review'],
  ['Use the results', 'Every value is in Documents, as files or as a sheet. Export to Excel, CSV or a database.', 'library'],
];

export function HelpDialogs({ open, close }: { open: HelpDialog; close: () => void }) {
  const { go, route, log, notify, me } = useApp();
  const [text, setText] = useState('');
  const [withPage, setWithPage] = useState(true);
  return (
    <>
      <Dialog open={open === 'start'} onOpenChange={(_, d) => !d.open && close()}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Get started</DialogTitle>
            <DialogContent>
              <ol className="steps">
                {STEPS.map(([title, body, page]) => (
                  <li key={page}>
                    <Text weight="semibold" block>{title}</Text>
                    <Body1 block>{body}</Body1>
                    <Button size="small" onClick={() => { close(); go(page); }}>{`Go to ${page === 'library' ? 'Documents' : page === 'extract' ? 'Extract' : 'Review'}`}</Button>
                  </li>
                ))}
              </ol>
            </DialogContent>
            <DialogActions><DialogTrigger disableButtonEnhancement><Button appearance="primary">Close</Button></DialogTrigger></DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>

      <Dialog open={open === 'shortcuts'} onOpenChange={(_, d) => !d.open && close()}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>Keyboard shortcuts</DialogTitle>
            <DialogContent>
              <RTable size="small" layout="auto" aria-label="Keyboard shortcuts">
                <RHeader><RRow><RHead>Where</RHead><RHead>Keys</RHead><RHead>Does</RHead></RRow></RHeader>
                <RBody>
                  {SHORTCUTS.map(([where, keys, does]) => (
                    <RRow key={where + keys}><RCell data-label="Where">{where}</RCell><RCell data-label="Keys"><kbd>{keys}</kbd></RCell><RCell data-label="Does">{does}</RCell></RRow>
                  ))}
                </RBody>
              </RTable>
            </DialogContent>
            <DialogActions><DialogTrigger disableButtonEnhancement><Button appearance="primary">Close</Button></DialogTrigger></DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>

      <Dialog open={open === 'feedback'} onOpenChange={(_, d) => !d.open && close()}>
        <DialogSurface>
          <form onSubmit={(e) => {
            e.preventDefault();
            log('Gave feedback on the app', withPage ? `Page: ${route.page}` : 'No page attached');
            notify('Thanks for your feedback', 'success', 'Your administrators can read it in the audit log.');
            setText('');
            close();
          }}>
            <DialogBody>
              <DialogTitle>Give feedback</DialogTitle>
              <DialogContent className="form">
                <Field label="What should we improve?" required><Textarea value={text} onChange={(_, d) => setText(d.value)} resize="vertical" rows={4} /></Field>
                <Checkbox checked={withPage} onChange={(_, d) => setWithPage(d.checked === true)} label="Include the page you're on" />
                <Caption1 className="muted">{`Sent to your administrators with your name, ${me.name}. No document contents are included.`}</Caption1>
              </DialogContent>
              <DialogActions>
                <Button appearance="primary" type="submit" disabled={!text.trim()}>Send</Button>
                <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
              </DialogActions>
            </DialogBody>
          </form>
        </DialogSurface>
      </Dialog>

      <Dialog open={open === 'about'} onOpenChange={(_, d) => !d.open && close()}>
        <DialogSurface>
          <DialogBody>
            <DialogTitle>About Document Extraction</DialogTitle>
            <DialogContent className="form">
              <Body1>{`Version ${__APP_VERSION__}, Harsha Engineers`}</Body1>
              <RTable size="small" layout="auto" aria-label="Engine versions">
                <RHeader><RRow><RHead>Engine</RHead><RHead>Component</RHead><RHead>Version</RHead></RRow></RHeader>
                <RBody>
                  {UPDATES.map((u) => <RRow key={u.name}><RCell data-label="Engine">{u.engine}</RCell><RCell data-label="Component">{u.name}</RCell><RCell data-label="Version">{u.current}</RCell></RRow>)}
                </RBody>
              </RTable>
            </DialogContent>
            <DialogActions><DialogTrigger disableButtonEnhancement><Button appearance="primary">Close</Button></DialogTrigger></DialogActions>
          </DialogBody>
        </DialogSurface>
      </Dialog>
    </>
  );
}

export function RemoveSamplesDialog({ close }: { close: () => void }) {
  const { removeSamples } = useApp();
  return (
    <Dialog modalType="alert" open onOpenChange={(_, d) => !d.open && close()}>
      <DialogSurface>
        <DialogBody>
          <DialogTitle>Remove sample data?</DialogTitle>
          <DialogContent>Sample batches, documents, database rows and notifications are removed. Profiles, engines and automations stay.</DialogContent>
          <DialogActions>
            <Button appearance="primary" onClick={() => { close(); removeSamples(); }}>Remove</Button>
            <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  );
}
