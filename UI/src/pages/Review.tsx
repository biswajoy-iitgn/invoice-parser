import { useEffect, useRef, useState } from 'react';
import {
  Button, Input, Text, Caption1, Body1, Field, Card, CardPreview, CardHeader, Link, ProgressBar, Badge,
  Tooltip, ToggleButton, Accordion, AccordionItem, AccordionHeader, AccordionPanel, Toolbar, ToolbarButton, ToolbarDivider,
  Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger, RadioGroup, Radio, Textarea, Checkbox,
  Skeleton, SkeletonItem, List, ListItem,
} from '@fluentui/react-components';
import {
  CheckmarkRegular, CheckmarkCircleFilled, ZoomInRegular, ZoomOutRegular, ChevronUpRegular, ChevronDownRegular, ChevronLeftRegular, ChevronRightRegular,
  SparkleRegular, ArrowLeftRegular, ThumbLikeRegular, ThumbDislikeRegular, ThumbLikeFilled, ThumbDislikeFilled, EyeRegular, ArrowUndoRegular,
} from '@fluentui/react-icons';
import { useApp, StatusBadge, EmptyState, pct, docChecks, Title, money, num, count } from '../ui';
import { usePaging } from '../sheet';
import { DOCS } from '../data';
import { stamp } from '../audit';
import { FileIcon, cardAction } from './Home';
import ExportMenu from './Export';
import { useControlStyles } from '../styles';
import type { Box, Doc, ProfileField, SampleField, SamplePage } from '../types';
import type { ReactNode } from 'react';

/** A profile field with what was read for it on this document. */
type Row = ProfileField & Partial<SampleField> & { missing: boolean; flagged: boolean };

const ISSUES: Record<string, string> = { wrong: "A value isn't what the document says", missing: 'A value is missing', field: 'A value is in the wrong field', other: 'Something else' };

function Feedback({ docName }: { docName: string }) {
  const { notify, log } = useApp();
  const [vote, setVote] = useState<'up' | 'down' | null>(null);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState('wrong');
  const [text, setText] = useState('');
  const [withPages, setWithPages] = useState(false);
  return (
    <div className="feedback">
      <Caption1>Was this accurate?</Caption1>
      <Tooltip content="Accurate" relationship="label">
        <ToggleButton size="small" appearance="subtle" checked={vote === 'up'} icon={vote === 'up' ? <ThumbLikeFilled /> : <ThumbLikeRegular />} onClick={() => { setVote('up'); log('Rated values accurate', docName); notify('Thanks, noted'); }} />
      </Tooltip>
      <Tooltip content="Not accurate" relationship="label">
        <ToggleButton size="small" appearance="subtle" checked={vote === 'down'} icon={vote === 'down' ? <ThumbDislikeFilled /> : <ThumbDislikeRegular />} onClick={() => setOpen(true)} />
      </Tooltip>
      <Dialog open={open} onOpenChange={(_, d) => setOpen(d.open)}>
        <DialogSurface>
          <form onSubmit={(e) => {
            e.preventDefault(); setVote('down'); setOpen(false);
            log('Reported a reading problem', `${docName}, ${ISSUES[kind]}${withPages ? ', with page images' : ''}`);
            notify('Thanks, your administrators will look at it');
          }}>
            <DialogBody>
              <DialogTitle>What went wrong?</DialogTitle>
              <DialogContent className="form">
                <Field label="Issue">
                  <RadioGroup value={kind} onChange={(_, d) => setKind(d.value)}>
                    {Object.entries(ISSUES).map(([k, v]) => <Radio key={k} value={k} label={v} />)}
                  </RadioGroup>
                </Field>
                <Field label="Details">
                  <Textarea value={text} onChange={(_, d) => setText(d.value)} resize="vertical" />
                </Field>
                <Checkbox checked={withPages} onChange={(_, d) => setWithPages(d.checked === true)} label={`Include the values and page images of ${docName}`} />
                <Caption1 className="muted">Goes to your administrators with your name. They decide whether it is used to improve the AI. Page images are only sent if you include them.</Caption1>
              </DialogContent>
              <DialogActions>
                <Button appearance="primary" type="submit">Send</Button>
                <DialogTrigger disableButtonEnhancement><Button>Cancel</Button></DialogTrigger>
              </DialogActions>
            </DialogBody>
          </form>
        </DialogSurface>
      </Dialog>
    </div>
  );
}

// Page image with a skeleton until it has loaded (wait UX: show structure, not a blank box).
function PageImage({ src, alt, w, h, children, zoom }: { src: string; alt: string; w: number; h: number; children?: ReactNode; zoom: number }) {
  const [loaded, setLoaded] = useState(false);
  return (
    <div className="page-img" style={{ width: `${zoom * 100}%`, aspectRatio: `${w} / ${h}` }} aria-busy={!loaded}>
      {!loaded && (
        <Skeleton className="page-skel" aria-label={'Loading page …'}>
          {[60, 40, 90, 80, 70, 95, 50, 85, 75].map((x, i) => <SkeletonItem key={i} style={{ width: `${x}%` }} />)}
        </Skeleton>
      )}
      <img src={src} alt={alt} onLoad={() => setLoaded(true)} className={loaded ? 'is-loaded' : ''} />
      {loaded && children}
    </div>
  );
}

// The exact spot on the page where the AI read a value, so the check needs no hunting.
function Snippet({ page, box, label }: { page: SamplePage; box: Box; label: string }) {
  const W = 320, H = 104; // target width; height cap so tall print doesn't dominate the card
  const [x0, y0, x1, y1] = box;
  const pad = Math.max(12, (y1 - y0) * 1.2); // a line of context above and below, faded at the edges
  const ry0 = Math.max(0, y0 - pad), ry1 = Math.min(page.h, y1 + pad), cx = (x0 + x1) / 2;
  const s = Math.min(W / (x1 - x0 + 56), H / (ry1 - ry0), 2.2); // digital pages are 160 dpi (2.2 px per point); past that the crop blurs
  // Value centred, so the printed label to its left stays in view
  return (
    <div className="snippet" style={{ height: (ry1 - ry0) * s }} role="img" aria-label={`Where ${label} is printed on the page`}>
      <div className="snippet-view">
        <img src={page.src} alt="" style={{ left: '50%', width: page.w * s, transform: `translate(${-cx * s}px, ${-ry0 * s}px)` }} draggable={false} />
        <span className="snippet-box" style={{ left: `calc(50% + ${(x0 - cx) * s}px)`, top: (y0 - ry0) * s, width: (x1 - x0) * s, height: (y1 - y0) * s }} />
      </div>
    </div>
  );
}

// The queue stays beside the document, like Outlook's message list beside the reading pane:
// one Tab stop, arrow keys move through it, Enter opens.
function Queue({ queue, current, openCount }: { queue: Doc[]; current: string; openCount: number }) {
  const { go, flagBelow } = useApp();
  const onRef = useRef<HTMLDivElement>(null);
  useEffect(() => { onRef.current?.scrollIntoView({ block: 'nearest' }); }, [current]);
  const waiting = queue.filter((d) => d.status === 'review').length;
  return (
    <nav className="queue" aria-labelledby="queue-h">
      <Text as="h2" id="queue-h" weight="semibold" className="queue-title">{`Needs review (${waiting})`}</Text>
      <List navigationMode="items" aria-labelledby="queue-h" className={useControlStyles().queueList}>
        {queue.map((d) => {
          const sample = DOCS[d.sample || d.id];
          const on = d.id === current;
          const left = on ? openCount : sample ? sample.fields.filter((f) => f.conf < flagBelow).length : 0;
          const note = d.status === 'accepted' ? 'Approved' : left ? `${count(left, 'value', 'values')} to check` : 'Ready to approve';
          return (
            <ListItem key={d.id} value={d.id} ref={on ? onRef : null} className={`queue-item${on ? ' is-on' : ''}`} aria-current={on ? 'page' : undefined}
              aria-label={`${d.name}, ${note}`} onAction={() => go('review', d.id)}>
              {sample ? <img src={sample.pages[0].thumb || sample.pages[0].src} alt="" className="queue-thumb" loading="lazy" decoding="async" draggable={false} /> : <FileIcon kind={d.kind} />}
              <span className="queue-text">
                <Text truncate wrap={false} block weight={on ? 'semibold' : 'regular'}>{d.name}</Text>
                <Caption1 className="muted">{note}</Caption1>
              </span>
            </ListItem>
          );
        })}
      </List>
    </nav>
  );
}

function Landing({ queue }: { queue: Doc[] }) {
  const { go, allDocs, profiles, flagBelow } = useApp();
  const paged = usePaging(queue, 'review', 'documents');
  const labels = Object.fromEntries(profiles.flatMap((p) => p.fields.map((f) => [f.key, f.label])));
  const approved = allDocs.filter((d) => ['accepted', 'exported'].includes(d.status)).length;
  return (
    <div className="page">
      <header className="page-header">
        <div className="page-heading">
          <Title>Review</Title>
          <Body1 className="muted">{`${queue.length ? `${queue.length} waiting, ` : ''}${approved} approved in the last 10 days`}</Body1>
        </div>
      </header>
      {queue.length === 0 ? (
        <div className="glow-growth rounded">
          <EmptyState picto="check_mark_button" title="You're all caught up" body="When the AI isn't sure about a value, the document waits here for you.">
            <Button appearance="primary" onClick={() => go('library')}>Browse documents</Button>
          </EmptyState>
        </div>
      ) : (
        <div className="file-cards wide">
          {paged.items.map((d) => {
            const sample = DOCS[d.sample || d.id];
            const flags = sample?.fields?.filter((f) => f.conf < flagBelow) || [];
            return (
              <Card key={d.id} className="file-card lift" {...cardAction(() => go('review', d.id), `Review ${d.name}`)}>
                {sample && <CardPreview className="file-card-preview"><img src={sample.pages[0].thumb || sample.pages[0].src} alt="" loading="lazy" decoding="async" /></CardPreview>}
                <CardHeader image={<FileIcon kind={d.kind} />} header={<Text weight="semibold" truncate wrap={false}>{d.name}</Text>}
                  description={<Caption1>{sample?.vendor || d.reason}</Caption1>} />
                {flags.length > 0 && <div className="flag-names"><EyeRegular aria-hidden /><Caption1>{`Check ${flags.map((f) => labels[f.key] || f.key).join(', ')}`}</Caption1></div>}
              </Card>
            );
          })}
        </div>
      )}
      {paged.pager}
    </div>
  );
}

export default function Review() {
  const { route, docRecord, allDocs, setRuns, profiles, go, notify, setEdits, flagBelow, me, log } = useApp();
  const rec = route.id ? docRecord(route.id) : null;
  const controls = useControlStyles();
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const [sel, setSel] = useState<string | null>(null);
  const [pageIx, setPageIx] = useState(0);
  const [zoom, setZoom] = useState(1);
  const boxRef = useRef<HTMLSpanElement>(null);
  useEffect(() => { boxRef.current?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' }); }, [sel, zoom, pageIx]);
  const queue = allDocs.filter((d) => d.status === 'review' || (d.id === rec?.id && !d.archived));

  if (!route.id) return <Landing queue={queue} />;
  if (!rec) {
    return (
      <div className="page">
        <EmptyState picto="page_with_curl" title="This document isn't available" body="It may belong to a batch that was removed.">
          <Button appearance="primary" onClick={() => go('review')}>Go to review</Button>
        </EmptyState>
      </div>
    );
  }

  const { sample, values } = rec;
  const ix = queue.findIndex((d) => d.id === rec.id);
  const top = (right: ReactNode) => (
    <div className="doc-head">
      <Tooltip content="All documents to review" relationship="label"><Button appearance="subtle" icon={<ArrowLeftRegular className="flip-rtl" />} onClick={() => go('review')} /></Tooltip>
      <div className="doc-title">
        <Title size="doc" className="doc-name">{rec.name}</Title>
        <Caption1 className="muted">{sample || rec.archived ? [values.vendor || values.supplier || values.consignee, rec.kind === 'vector' ? 'digital PDF' : 'photo'].filter(Boolean).join(', ') : rec.reason}</Caption1>
      </div>
      {ix >= 0 && queue.length > 1 && (
        <div className="row nowrap pager">
          <Tooltip content="Previous document" relationship="label"><Button appearance="subtle" icon={<ChevronUpRegular />} disabled={ix <= 0} onClick={() => go('review', queue[ix - 1].id)} /></Tooltip>
          <Caption1>{`${ix + 1} of ${queue.length}`}</Caption1>
          <Tooltip content="Next document" relationship="label"><Button appearance="subtle" icon={<ChevronDownRegular />} disabled={ix >= queue.length - 1} onClick={() => go('review', queue[ix + 1].id)} /></Tooltip>
        </div>
      )}
      {right}
    </div>
  );

  if (!sample) {
    // An archived original keeps its values; an upload the reader couldn't read says what to try.
    const fields = (profiles.find((p) => p.id === rec.profile) || profiles[0]).fields.filter((f) => f.enabled && values[f.key]);
    return (
      <div className="doc-view">
        {top(rec.archived ? <div className="row nowrap doc-actions"><ExportMenu scope={{ docs: [rec] }} /><StatusBadge status={rec.status} /></div> : null)}
        <div className="doc-body">
          <section className="canvas" aria-label={rec.archived ? 'Original file' : 'Uploaded file'}>
            <div className="canvas-scroll">
              {rec.archived && <EmptyState compact picto="card_index_dividers" title="Original archived" body={`The file was archived after it was ${rec.status === 'exported' ? 'exported' : 'approved'}. Its values stay here.`} />}
              {!rec.archived && rec.blob?.type?.startsWith('image/') && <img src={rec.blob.url} alt={rec.name} className="upload-img" />}
              {!rec.archived && rec.blob?.type === 'application/pdf' && <iframe src={rec.blob.url} title={rec.name} className="upload-pdf" />}
              {!rec.archived && !rec.blob && <EmptyState compact picto="page_with_curl" title="Preview not available" body="This file can't be shown here. Its batch still has its details." />}
            </div>
          </section>
          <aside className="values" aria-label="Extracted values">
            {rec.archived ? (
              <>
                <Title as="h2" size="pane">{count(fields.length, 'value', 'values')}</Title>
                <dl className="kv read-only">
                  {fields.map((f) => <div key={f.key}><dt><Caption1 className="muted">{f.label}</Caption1></dt><dd><Text className={f.type === 'Amount' ? 'num' : ''}>{f.type === 'Amount' ? money(num(values[f.key])) : values[f.key]}</Text></dd></div>)}
                </dl>
              </>
            ) : (
              <EmptyState compact picto="page_with_curl" title="Couldn't read this file" body="Check that it isn't blank, password-protected, or cut off, then retry it from its batch.">
                <Button appearance="primary" onClick={() => go('run', rec.run)}>Go to batch</Button>
              </EmptyState>
            )}
          </aside>
        </div>
      </div>
    );
  }

  const profile = profiles.find((p) => p.id === rec.profile) || profiles[0];
  const fieldInfo: Record<string, SampleField> = Object.fromEntries(sample.fields.map((f) => [f.key, f]));
  const original: Record<string, string> = Object.fromEntries(sample.fields.map((f) => [f.key, f.value]));
  const rows = profile.fields.filter((f) => f.enabled).map((f): Row => {
    const info = fieldInfo[f.key];
    return { ...f, ...info, missing: !info, flagged: info ? info.conf < flagBelow : f.required };
  });
  const changed = (r: Row) => (values[r.key] ?? '') !== (original[r.key] ?? '');
  const resolved = (r: Row) => !r.flagged || confirmed[r.key] || changed(r);
  const flagged = rows.filter((r) => r.flagged);
  const open = flagged.filter((r) => !resolved(r));
  const done = flagged.filter((r) => resolved(r));
  const others = rows.filter((r) => !r.flagged && !r.missing);
  const page = sample.pages[pageIx];
  const checks = docChecks(sample, values);
  const passed = checks.filter((c) => c.result === 'pass').length;
  const setValue = (k: string, v: string) => setEdits((all) => ({ ...all, [rec.id]: { ...(all[rec.id] || {}), [k]: v } }));
  const select = (r: Row) => { setSel(r.key); if (r.box && r.page != null && r.page !== pageIx) setPageIx(r.page); };
  const accepted = rec.status === 'accepted';

  const approve = () => {
    // the record of approval: who, when, and which doubtful values they checked or corrected
    const approval = { by: me.name, at: stamp(), checked: done.filter((r) => !changed(r)).map((r) => r.key), changed: rows.filter(changed).map((r) => r.key) };
    setRuns((all) => all.map((r) => (r.id !== rec.run ? r : { ...r, docs: r.docs.map((d) => (d.id === rec.id ? { ...d, status: 'accepted', approval, edited: d.edited || approval.changed.length > 0 } : d)) })));
    log('Approved document', `${rec.name}${approval.changed.length ? `, corrected ${approval.changed.map((k) => rows.find((r) => r.key === k)?.label).join(', ')}` : ''}`);
    const next = queue.find((d) => d.id !== rec.id);
    notify(`${rec.name} approved`, 'success', next ? `Next up: ${next.name}` : 'That was the last one');
    go('review', next?.id);
  };

  return (
    <div className="doc-view">
      {top(
        <div className="row nowrap doc-actions">
          {!accepted && flagged.length > 0 && (
            <div className="progress-mini" aria-live="polite">
              <Caption1 id="approve-why">{open.length ? `Check ${count(open.length, done.length ? 'more value' : 'value', done.length ? 'more values' : 'values')} to approve` : 'Ready to approve'}</Caption1>
              <ProgressBar value={done.length / flagged.length} color={open.length ? 'brand' : 'success'} thickness="large" aria-label="Checked values" />
            </div>
          )}
          <ExportMenu scope={{ docs: [rec], values, rows }} />
          {accepted ? <StatusBadge status="accepted" /> : open.length > 0 ? (
            <Button appearance="primary" icon={<CheckmarkRegular />} disabledFocusable aria-describedby="approve-why">Approve</Button>
          ) : (
            <Tooltip content="Mark this document as correct" relationship="description">
              <Button appearance="primary" icon={<CheckmarkRegular />} onClick={approve}>Approve</Button>
            </Tooltip>
          )}
        </div>,
      )}

      <div className="doc-body has-queue">
        <Queue queue={queue} current={rec.id} openCount={open.length} />
        <section className="canvas" aria-label="Document">
          <Toolbar aria-label="Page" size="small" className="page-toolbar">
            {sample.pages.length > 1 && (
              <>
                <Tooltip content="Previous page" relationship="label"><ToolbarButton icon={<ChevronLeftRegular className="flip-rtl" />} disabled={pageIx === 0} onClick={() => setPageIx(pageIx - 1)} /></Tooltip>
                <Caption1 className="nowrap">{`Page ${pageIx + 1} of ${sample.pages.length}`}</Caption1>
                <Tooltip content="Next page" relationship="label"><ToolbarButton icon={<ChevronRightRegular className="flip-rtl" />} disabled={pageIx >= sample.pages.length - 1} onClick={() => setPageIx(pageIx + 1)} /></Tooltip>
                <ToolbarDivider />
              </>
            )}
            <Tooltip content="Zoom out" relationship="label"><ToolbarButton icon={<ZoomOutRegular />} disabled={zoom <= 1} onClick={() => setZoom(zoom - 0.5)} /></Tooltip>
            <Caption1 className="nowrap">{`${zoom * 100}%`}</Caption1>
            <Tooltip content="Zoom in" relationship="label"><ToolbarButton icon={<ZoomInRegular />} disabled={zoom >= 3} onClick={() => setZoom(zoom + 0.5)} /></Tooltip>
          </Toolbar>
          <div className="canvas-scroll" role="region" tabIndex={0} aria-label="Page view, scrollable">
            <PageImage key={page.src} src={page.src} w={page.w} h={page.h} zoom={zoom} alt={`${rec.name}, page ${pageIx + 1} of ${sample.pages.length}`}>
              {!accepted && rows.filter((r) => r.box && r.page === pageIx && !resolved(r)).map((r) => {
                const [x0, y0, x1, y1] = r.box as Box;
                return (
                  <span key={r.key} ref={r.key === sel ? boxRef : null} className={`box${r.key === sel ? ' is-sel' : ' is-flag'}`} aria-hidden="true"
                    style={{ left: `${(x0 / page.w) * 100}%`, top: `${(y0 / page.h) * 100}%`, width: `${((x1 - x0) / page.w) * 100}%`, height: `${((y1 - y0) / page.h) * 100}%` }} />
                );
              })}
            </PageImage>
          </div>
        </section>

        <aside className="values" aria-label="Extracted values">
          {accepted && (
            <div className="seal-band">
              <CheckmarkCircleFilled fontSize={32} aria-hidden />
              <div>
                <Text weight="semibold" block>{rec.approval ? `Approved by ${rec.approval.by}` : 'Approved automatically'}</Text>
                <Caption1>{rec.approval ? rec.approval.at : 'Every value was read above the check level and every check passed.'}</Caption1>
              </div>
            </div>
          )}
          {accepted && rec.approval && (rec.approval.checked.length + rec.approval.changed.length) > 0 && (
            <section aria-labelledby="approved-h" className="stack-s">
              <Text as="h2" id="approved-h" size={200} weight="semibold" className="muted">Checked before approval</Text>
              <ul className="checked">
                {[...rec.approval.changed, ...rec.approval.checked].map((k) => {
                  const r = rows.find((x) => x.key === k);
                  const fixed = rec.approval?.changed.includes(k);
                  return r && (
                    <li key={k}>
                      <CheckmarkCircleFilled className="ok-icon" aria-hidden />
                      <span className="checked-text">
                        <Caption1 block className="muted">{fixed ? `${r.label}, corrected from ${original[k] || 'blank'}` : `${r.label}, confirmed`}</Caption1>
                        <Text block>{values[k]}</Text>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
          <div className="ai-note"><SparkleRegular aria-hidden /><Caption1>Read by AI. AI-generated content may be incorrect. Check for accuracy.</Caption1></div>

          {!accepted && open.length > 0 && (
            <section className="stack-s" aria-labelledby="tocheck-h">
              <Title as="h2" size="pane" id="tocheck-h">{open.length === 1 ? 'Check this value' : `Check ${open.length} values`}</Title>
              {open.map((r) => (
                <Card key={r.key} className={`check-card${r.key === sel ? ' is-sel' : ''}`} onFocus={() => select(r)}>
                  <div className="row between">
                    <Text weight="semibold">{r.label}</Text>
                    <Badge appearance="tint" color="informative" shape="rounded">{r.conf != null ? `${pct(r.conf)} sure` : 'Not found'}</Badge>
                  </div>
                  {r.box && <Snippet page={sample.pages[r.page ?? 0]} box={r.box} label={r.label} />}
                  <Field hint={r.note || (r.missing ? 'Not found on the document' : undefined)}>
                    <Input value={values[r.key] ?? ''} aria-label={r.label} placeholder={r.missing ? 'Type the value' : ''} onChange={(_, d) => setValue(r.key, d.value)} />
                  </Field>
                  <div className="row">
                    <Button size="small" appearance="primary" icon={<CheckmarkRegular />} onClick={(e) => { e.stopPropagation(); setConfirmed({ ...confirmed, [r.key]: true }); setSel(null); }}>Looks right</Button>
                    {r.box && <Button appearance="transparent" size="small" icon={<EyeRegular />} onClick={(e) => { e.stopPropagation(); select(r); }}>Show on page</Button>}
                  </div>
                </Card>
              ))}
            </section>
          )}

          {!accepted && flagged.length > 0 && open.length === 0 && (
            <div className="ready glow-growth">
              <CheckmarkCircleFilled className="ready-icon pop" fontSize={28} aria-hidden />
              <div><Text weight="semibold" block>All checked</Text><Caption1>Approve saves the values for export.</Caption1></div>
            </div>
          )}

          {done.length > 0 && !accepted && (
            <section aria-labelledby="checked-h" className="stack-s">
              <Text as="h2" id="checked-h" size={200} weight="semibold" className="muted">Checked</Text>
              <ul className="checked">
                {done.map((r) => (
                  <li key={r.key}>
                    <CheckmarkCircleFilled className="ok-icon" aria-hidden />
                    <span className="checked-text"><Caption1 block className="muted">{r.label}</Caption1><Text block>{values[r.key]}</Text></span>
                    <Tooltip content={`Check ${r.label} again`} relationship="label">
                      <Button size="small" appearance="subtle" icon={<ArrowUndoRegular />} onClick={() => { setConfirmed({ ...confirmed, [r.key]: false }); setValue(r.key, original[r.key]); }} />
                    </Tooltip>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <Accordion collapsible multiple>
            <AccordionItem value="all">
              <AccordionHeader size="small">{`Other values (${others.length})`}</AccordionHeader>
              <AccordionPanel>
                <dl className="kv">
                  {others.map((r) => (
                    <div key={r.key} className={r.key === sel ? 'is-sel' : ''}>
                      <dt><Caption1 className="muted">{r.label}</Caption1></dt>
                      <dd><Input size="small" appearance="underline" className={controls.fullWidth} value={values[r.key] ?? ''} aria-label={r.label} onFocus={() => select(r)} onChange={(_, d) => setValue(r.key, d.value)} /></dd>
                    </div>
                  ))}
                </dl>
              </AccordionPanel>
            </AccordionItem>
            <AccordionItem value="checks">
              <AccordionHeader size="small">{`Checks, ${passed} of ${checks.length} passed`}</AccordionHeader>
              <AccordionPanel>
                <ul className="list">
                  {checks.map((c) => (
                    <li key={c.name}>
                      <div className="list-main"><Text size={200}>{c.name}</Text><Caption1 className="muted">{c.detail}</Caption1></div>
                      <StatusBadge status={c.result} />
                    </li>
                  ))}
                </ul>
              </AccordionPanel>
            </AccordionItem>
          </Accordion>
          <Feedback docName={rec.name} />
          {!accepted && <Link className="skip-doc" onClick={() => go('review', queue[ix + 1]?.id)}>{queue[ix + 1] ? `Skip for now, open ${queue[ix + 1].name}` : 'Back to all documents'}</Link>}
        </aside>
      </div>
    </div>
  );
}
