import { Fragment } from 'react';
import {
  Button, Card, Table, TableHeader, TableRow, TableHeaderCell, TableBody, TableCell, Link, ProgressBar, Text, Field, Caption1,
  Menu, MenuTrigger, MenuPopover, MenuList, MenuItem, MenuButton, Breadcrumb, BreadcrumbItem, BreadcrumbButton, BreadcrumbDivider,
} from '@fluentui/react-components';
import { ArrowRepeatAllRegular, DocumentSearchRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, StatusBadge, EmptyState, pct, Title } from '../ui';
import { PATHS } from '../data';
import ExportMenu from './Export';
import { FileIcon } from './Home';

export const profileName = (profiles, id) => (id === 'auto' ? 'Auto-detect' : profiles.find((p) => p.id === id)?.name || '');

export function Crumbs({ items }) {
  const { go } = useApp();
  return (
    <Breadcrumb aria-label="Breadcrumb" size="small">
      {items.map(([label, target], i) => (
        <Fragment key={label}>
          <BreadcrumbItem>
            {target ? <BreadcrumbButton onClick={() => go(...target)}>{label}</BreadcrumbButton> : <BreadcrumbButton current>{label}</BreadcrumbButton>}
          </BreadcrumbItem>
          {i < items.length - 1 && <BreadcrumbDivider />}
        </Fragment>
      ))}
    </Breadcrumb>
  );
}

export function RunDetail() {
  const { runs, setRuns, profiles, route, go, notify } = useApp();
  const run = runs.find((r) => r.id === route.id);
  if (!run) {
    return (
      <div className="page">
        <EmptyState picto="inbox_tray" title="This batch isn't available" body="It may have been removed. Your other batches are still here.">
          <Button appearance="primary" onClick={() => go('runs')}>Go to batches</Button>
        </EmptyState>
      </div>
    );
  }
  const count = (s) => run.docs.filter((d) => d.status === s).length;
  const finished = run.docs.filter((d) => ['accepted', 'review', 'failed'].includes(d.status)).length;
  const running = run.status === 'running';
  const nextReview = run.docs.find((d) => d.status === 'review');

  const retry = (doc, higher) => {
    setRuns((all) => all.map((r) => (r.id !== run.id ? r : { ...r, docs: r.docs.map((d) => (d.id === doc.id ? { ...d, reason: higher ? 'Retried at high quality; the demo reads only the bundled sample invoices' : 'Retried; the demo reads only the bundled sample invoices' } : d)) })));
    notify(higher ? 'Retried with high quality and image enhancement' : 'Retried with the same settings', 'warning', 'The demo can only read the bundled sample invoices');
  };

  return (
    <div className="page">
      <PageHeader title={run.source} crumbs={<Crumbs items={[['Documents', ['library']], [`Batch ${run.id}`]]} />}>
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
          <Text>{`${count('accepted')} accepted, ${count('review')} need review, ${count('failed')} failed`}</Text>
        </section>
      )}
      <div role="status" aria-live="polite" className="sr-only">{running ? `${finished} of ${run.docs.length} documents done` : `Batch finished. ${count('review')} need review.`}</div>

      <Caption1 className="muted">{`${profileName(profiles, run.profile)}, uploaded by ${run.by}, ${run.started}`}</Caption1>

      <Card className="list-card">
        <Table aria-label="Documents in this batch" className="responsive list-table">
          <TableHeader>
            <TableRow>
              <TableHeaderCell>Name</TableHeaderCell><TableHeaderCell>Detected as</TableHeaderCell>
              {running && <TableHeaderCell>Progress</TableHeaderCell>}<TableHeaderCell>Status</TableHeaderCell><TableHeaderCell>Accuracy</TableHeaderCell>
            </TableRow>
          </TableHeader>
          <TableBody>
            {run.docs.map((d) => {
              const path = PATHS[d.kind];
              return (
                <TableRow key={d.id}>
                  <TableCell data-label="Document">
                    <div className="name-cell">
                      <FileIcon kind={d.kind} />
                      <div className="cell-stack">
                        <Link appearance="subtle" onClick={() => go('review', d.id)}>{d.name}</Link>
                        {d.reason && !['queued', 'running'].includes(d.status) && <Caption1 className="muted">{d.reason}</Caption1>}
                      </div>
                    </div>
                  </TableCell>
                  <TableCell data-label="Detected as">{d.status === 'queued' ? '' : `${profileName(profiles, d.profile)}${d.route ? `, ${pct(d.route)}` : ''}`}</TableCell>
                  {running && <TableCell data-label="Progress">
                    {d.status === 'running'
                      ? <div className="stage"><Caption1>{`${path[d.step] || path[0]}\u00a0…`}</Caption1><ProgressBar value={(d.step + 1) / path.length} aria-label={`${d.name} progress`} /></div>
                      : d.status === 'queued' ? <Caption1 className="muted">Waiting</Caption1> : <Caption1>Done</Caption1>}
                  </TableCell>}
                  <TableCell data-label="Status">
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
                  </TableCell>
                  <TableCell data-label="Accuracy">{pct(d.score)}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
