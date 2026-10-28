import { Fragment } from 'react';
import {
  Button, Card, ProgressBar, Text, Field, Caption1,
  Menu, MenuTrigger, MenuPopover, MenuList, MenuItem, MenuButton, Breadcrumb, BreadcrumbItem, BreadcrumbButton, BreadcrumbDivider,
} from '@fluentui/react-components';
import { ArrowRepeatAllRegular, DocumentSearchRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, StatusBadge, EmptyState, pct, Title, RHead, RHeader, RBody, RRow, RCell, RTable, CellLink } from '../ui';
import { PATHS } from '../data';
import ExportMenu from './Export';
import { FileIcon } from './Home';
import type { DocStatus, Profile, RunDoc } from '../types';

export const profileName = (profiles: Profile[], id: string): string => (id === 'auto' ? 'Auto-detect' : profiles.find((p) => p.id === id)?.name || '');

export function Crumbs({ items }: { items: [label: string, target?: [string, string?]][] }) {
  const { go } = useApp();
  return (
    <Breadcrumb aria-label="Breadcrumb" size="small">
      {items.map(([label, target], i) => (
        <Fragment key={label}>
          <BreadcrumbItem>
            {target ? <BreadcrumbButton onClick={() => go(target[0], target[1])}>{label}</BreadcrumbButton> : <BreadcrumbButton current>{label}</BreadcrumbButton>}
          </BreadcrumbItem>
          {i < items.length - 1 && <BreadcrumbDivider />}
        </Fragment>
      ))}
    </Breadcrumb>
  );
}

export function RunDetail() {
  const { runs, setRuns, profiles, route, go, notify, log } = useApp();
  const run = runs.find((r) => r.id === route.id);
  if (!run) {
    return (
      <div className="page">
        <EmptyState picto="inbox_tray" title="This batch isn't available" body="It may have been removed. Your other batches are still here.">
          <Button appearance="primary" onClick={() => go('extract')}>Go to Extract</Button>
        </EmptyState>
      </div>
    );
  }
  const count = (...s: DocStatus[]) => run.docs.filter((d) => s.includes(d.status)).length;
  const finished = count('accepted', 'exported', 'review', 'failed');
  const running = run.status === 'running';
  const nextReview = run.docs.find((d) => d.status === 'review');

  // Reads the file again, then shows what the reader returned this time.
  const retry = (doc: RunDoc, higher: boolean) => {
    const patch = (p: Partial<RunDoc>) => setRuns((all) => all.map((r) => (r.id !== run.id ? r : { ...r, docs: r.docs.map((d) => (d.id === doc.id ? { ...d, ...p } : d)) })));
    patch({ status: 'running', reason: '' });
    log(higher ? 'Read a file again at high quality' : 'Read a file again', `${run.id}, ${doc.name}`);
    setTimeout(() => {
      patch({ status: 'failed', reason: higher ? "Couldn't read it at high quality either" : "Couldn't read it again" });
      notify(`${doc.name} still couldn't be read`, 'warning', "Check that the file isn't blank, password-protected, or cut off.");
    }, 1500);
  };

  return (
    <div className="page">
      <PageHeader title={run.source.replace(/^Folder: /, '')} crumbs={<Crumbs items={[['Extract', ['extract']], [`Batch ${run.id}`]]} />}>
        {!running && <ExportMenu scope={run} />}
        {nextReview && <Button appearance="primary" icon={<DocumentSearchRegular />} onClick={() => go('review', nextReview.id)}>Review next</Button>}
      </PageHeader>

      {running ? (
        <Field label={'Reading documents\u00a0…'} validationState="none" validationMessage={`${finished} of ${run.docs.length} documents read`}>
          <ProgressBar value={finished / run.docs.length} thickness="large" />
        </Field>
      ) : (
        <section className="done-band bokashi-growth" aria-labelledby="done-h">
          <Title as="h2" size="section" id="done-h">Batch finished</Title>
          <Text>{`${count('accepted', 'exported')} accepted, ${count('review')} need review, ${count('failed')} failed`}</Text>
        </section>
      )}
      <div role="status" aria-live="polite" className="sr-only">{running ? `${finished} of ${run.docs.length} documents done` : `Batch finished. ${count('review')} need review.`}</div>

      <Caption1 className="muted">{`${profileName(profiles, run.profile)}, uploaded by ${run.by}, ${run.started}`}</Caption1>

      <Card className="list-card">
        <RTable aria-label="Documents in this batch" list>
          <RHeader>
            <RRow>
              <RHead>Name</RHead><RHead>Detected as</RHead>
              {running && <RHead>Progress</RHead>}<RHead>Status</RHead><RHead>Confidence</RHead>
            </RRow>
          </RHeader>
          <RBody>
            {run.docs.map((d) => {
              const path = PATHS[d.kind];
              return (
                <RRow key={d.id}>
                  <RCell data-label="Document">
                    <div className="name-cell">
                      <FileIcon kind={d.kind} />
                      <div className="cell-stack">
                        <CellLink onClick={() => go('review', d.id)}>{d.name}</CellLink>
                        {d.reason && !['queued', 'running'].includes(d.status) && <Caption1 className="muted">{d.reason}</Caption1>}
                      </div>
                    </div>
                  </RCell>
                  <RCell data-label="Detected as">{d.status === 'queued' ? '' : d.status === 'failed' && !d.sample && !d.values ? 'Not detected' : `${profileName(profiles, d.profile)}${d.route ? `, ${pct(d.route)}` : ''}`}</RCell>
                  {running && <RCell data-label="Progress">
                    {d.status === 'running'
                      ? <div className="stage"><Caption1>{`${path[d.step ?? 0] || path[0]}\u00a0…`}</Caption1><ProgressBar value={((d.step ?? 0) + 1) / path.length} aria-label={`${d.name} progress`} /></div>
                      : d.status === 'queued' ? <Caption1 className="muted">Waiting</Caption1> : <Caption1>Done</Caption1>}
                  </RCell>}
                  <RCell data-label="Status">
                    <div className="row">
                      <StatusBadge status={d.status} />
                      {d.status === 'failed' && (
                        <Menu>
                          <MenuTrigger disableButtonEnhancement>
                            <MenuButton size="small" appearance="subtle" icon={<ArrowRepeatAllRegular />}>Retry</MenuButton>
                          </MenuTrigger>
                          <MenuPopover>
                            <MenuList>
                              <MenuItem onClick={() => retry(d, false)}>Retry with same settings</MenuItem>
                              <MenuItem onClick={() => retry(d, true)}>Retry with high quality</MenuItem>
                            </MenuList>
                          </MenuPopover>
                        </Menu>
                      )}
                    </div>
                  </RCell>
                  <RCell data-label="Confidence">{pct(d.score)}</RCell>
                </RRow>
              );
            })}
          </RBody>
        </RTable>
      </Card>
    </div>
  );
}
