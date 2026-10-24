import { useEffect, useRef, useState } from 'react';
import {
  Button, Input, Text, Caption1, Body1, Field, Card, CardPreview, CardHeader, Link, ProgressBar, Badge,
  Tooltip, ToggleButton, Accordion, AccordionItem, AccordionHeader, AccordionPanel, Toolbar, ToolbarButton, ToolbarDivider,
  Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, DialogTrigger, RadioGroup, Radio, Textarea,
  Skeleton, SkeletonItem,
} from '@fluentui/react-components';
import {
  CheckmarkRegular, CheckmarkCircleFilled, ZoomInRegular, ZoomOutRegular, ChevronUpRegular, ChevronDownRegular, ChevronLeftRegular, ChevronRightRegular,
  SparkleRegular, ArrowLeftRegular, WarningRegular, ThumbLikeRegular, ThumbDislikeRegular, ThumbLikeFilled, ThumbDislikeFilled, EyeRegular, ArrowUndoRegular,
} from '@fluentui/react-icons';
import { useApp, StatusBadge, EmptyState, pct, docChecks, Title, Picto } from '../ui';
import { DOCS, FLAG_BELOW } from '../data';
import { FileIcon, cardAction } from './Home';
import ExportMenu from './Export';

function Feedback({ docName }) {
  const { notify } = useApp();
  const [vote, setVote] = useState(null);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState('wrong');
  const [text, setText] = useState('');
  return (
    <div className="feedback">
      <Caption1>Was this accurate?</Caption1>
      <Tooltip content="Accurate" relationship="label">
        <ToggleButton size="small" appearance="subtle" checked={vote === 'up'} icon={vote === 'up' ? <ThumbLikeFilled /> : <ThumbLikeRegular />} onClick={() => { setVote('up'); notify('Feedback sent'); }} />
      </Tooltip>
      <Tooltip content="Not accurate" relationship="label">
        <ToggleButton size="small" appearance="subtle" checked={vote === 'down'} icon={vote === 'down' ? <ThumbDislikeFilled /> : <ThumbDislikeRegular />} onClick={() => setOpen(true)} />
      </Tooltip>
      <Dialog open={open} onOpenChange={(_, d) => setOpen(d.open)}>
        <DialogSurface>
          <form onSubmit={(e) => { e.preventDefault(); setVote('down'); setOpen(false); notify('Feedback sent', 'success', 'It goes to the team that tunes the AI model'); }}>
            <DialogBody>
              <DialogTitle>What went wrong?</DialogTitle>
              <DialogContent className="form">
                <Field label="Issue">
                  <RadioGroup value={kind} onChange={(_, d) => setKind(d.value)}>
                    <Radio value="wrong" label="A value isn't what the document says" />
                    <Radio value="missing" label="A value is missing" />
                    <Radio value="field" label="A value is in the wrong field" />
                    <Radio value="other" label="Something else" />
                  </RadioGroup>
                </Field>
                <Field label="Details" hint={`The values and page images of ${docName} are sent with your feedback`}>
                  <Textarea value={text} onChange={(_, d) => setText(d.value)} resize="vertical" />
                </Field>
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
function PageImage({ src, alt, w, h, children, zoom }) {
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
function Snippet({ page, box, label }) {
  const W = 320;
  const [x0, y0, x1, y1] = box;
  const ry0 = Math.max(0, y0 - 9), ry1 = Math.min(page.h, y1 + 9), cx = (x0 + x1) / 2;
  const s = Math.min(W / (x1 - x0 + 56), 1.6); // pages are 110 dpi; past 1.6x the crop blurs
  // Value centred, so the printed label to its left stays in view
  return (
    <div className="snippet" style={{ height: (ry1 - ry0) * s }} role="img" aria-label={`Where ${label} is printed on the page`}>
      <img src={page.src} alt="" style={{ left: '50%', width: page.w * s, transform: `translate(${-cx * s}px, ${-ry0 * s}px)` }} draggable={false} />
      <span className="snippet-box" style={{ left: `calc(50% + ${(x0 - cx) * s}px)`, top: (y0 - ry0) * s, width: (x1 - x0) * s, height: (y1 - y0) * s }} />
    </div>
  );
}

function Landing({ queue }) {
  const { go, allDocs, profiles } = useApp();
  const labels = Object.fromEntries(profiles.flatMap((p) => p.fields.map((f) => [f.key, f.label])));
  const approved = allDocs.filter((d) => d.status === 'accepted').length;
  return (
    <div className="page">
      <header className="page-header">
        <div className="page-heading">
          <Title>Review</Title>
          <Body1 className="muted">{queue.length ? `${queue.length} waiting, ${approved} approved so far` : `${approved} approved so far`}</Body1>
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
          {queue.map((d) => {
            const sample = DOCS[d.sample || d.id];
            const flags = sample?.fields?.filter((f) => f.conf < FLAG_BELOW) || [];
            return (
              <Card key={d.id} className="file-card lift" {...cardAction(() => go('review', d.id), `Review ${d.name}`)}>
                {sample && <CardPreview className="file-card-preview"><img src={sample.pages[0].src} alt="" /></CardPreview>}
                <CardHeader image={<FileIcon kind={d.kind} />} header={<Text weight="semibold" truncate wrap={false}>{d.name}</Text>}
                  description={<Caption1>{sample?.vendor || d.reason}</Caption1>} />
                {flags.length > 0 && <div className="flag-names"><WarningRegular aria-hidden /><Caption1>{`Check ${flags.map((f) => labels[f.key] || f.key).join(', ')}`}</Caption1></div>}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function Review() {
  const { route, docRecord, allDocs, setRuns, profiles, go, notify, setEdits } = useApp();
  const rec = route.id ? docRecord(route.id) : null;
  const [confirmed, setConfirmed] = useState({});
  const [sel, setSel] = useState(null);
  const [pageIx, setPageIx] = useState(0);
  const [zoom, setZoom] = useState(1);
  const boxRef = useRef(null);
  useEffect(() => { boxRef.current?.scrollIntoView({ block: 'center', inline: 'center', behavior: 'smooth' }); }, [sel, zoom, pageIx]);
  const queue = allDocs.filter((d) => d.status === 'review' || d.id === rec?.id);

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
  const top = (right) => (
    <div className="doc-head">
      <Tooltip content="All documents to review" relationship="label"><Button appearance="subtle" icon={<ArrowLeftRegular />} onClick={() => go('review')} /></Tooltip>
      <div className="doc-title">
        <Title size="doc" className="doc-name">{rec.name}</Title>
        <Caption1 className="muted">{sample ? `${sample.vendor}, ${rec.kind === 'vector' ? 'digital PDF' : 'photo'}` : rec.reason}</Caption1>
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
    return (
      <div className="doc-view">
        {top(null)}
        <div className="doc-body">
          <section className="canvas" aria-label="Uploaded file">
            <div className="canvas-scroll">
              {rec.blob?.type?.startsWith('image/') && <img src={rec.blob.url} alt={rec.name} className="upload-img" />}
              {rec.blob?.type === 'application/pdf' && <iframe src={rec.blob.url} title={rec.name} className="upload-pdf" />}
              {!rec.blob && <EmptyState compact picto="page_with_curl" title="Preview not available" body="The original file isn't kept after you reload the page." />}
            </div>
          </section>
          <aside className="values">
            <EmptyState compact picto="page_with_curl" title="No values found" body="This demo reads only the bundled sample invoices.">
              <Button appearance="primary" onClick={() => go('home')}>Go to Home</Button>
            </EmptyState>
          </aside>
        </div>
      </div>
    );
  }

  const profile = profiles.find((p) => p.id === rec.profile) || profiles[0];
  const fieldInfo = Object.fromEntries(sample.fields.map((f) => [f.key, f]));
  const original = Object.fromEntries(sample.fields.map((f) => [f.key, f.value]));
  const rows = profile.fields.filter((f) => f.enabled).map((f) => {
    const info = fieldInfo[f.key];
    return { ...f, ...info, missing: !info, flagged: info ? info.conf < FLAG_BELOW : f.required };
  });
  const changed = (r) => (values[r.key] ?? '') !== (original[r.key] ?? '');
  const resolved = (r) => !r.flagged || confirmed[r.key] || changed(r);
  const flagged = rows.filter((r) => r.flagged);
  const open = flagged.filter((r) => !resolved(r));
  const done = flagged.filter((r) => resolved(r));
  const others = rows.filter((r) => !r.flagged && !r.missing);
  const page = sample.pages[pageIx];
  const checks = docChecks(sample, values);
  const passed = checks.filter((c) => c.result === 'pass').length;
  const setValue = (k, v) => setEdits((all) => ({ ...all, [rec.id]: { ...(all[rec.id] || {}), [k]: v } }));
  const select = (r) => { setSel(r.key); if (r.box && r.page !== pageIx) setPageIx(r.page); };
  const accepted = rec.status === 'accepted';

  const approve = () => {
    setRuns((all) => all.map((r) => (r.id !== rec.run ? r : { ...r, docs: r.docs.map((d) => (d.id === rec.id ? { ...d, status: 'accepted' } : d)) })));
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
              <Caption1>{`${done.length} of ${flagged.length} checked`}</Caption1>
              <ProgressBar value={done.length / flagged.length} color={open.length ? 'brand' : 'success'} thickness="large" aria-label="Checked values" />
            </div>
          )}
          <ExportMenu scope={{ docs: [rec], values, rows }} />
          {accepted ? <StatusBadge status="accepted" /> : (
            <Tooltip content={open.length ? `Check ${open.length} more ${open.length === 1 ? 'value' : 'values'} first` : 'Mark this document as correct'} relationship="description">
              <Button appearance="primary" icon={<CheckmarkRegular />} disabledFocusable={open.length > 0} onClick={approve}>Approve</Button>
            </Tooltip>
          )}
        </div>,
      )}

      <div className="doc-body">
        <section className="canvas" aria-label="Document">
          <div className="canvas-scroll" tabIndex={0} aria-label="Page view, scrollable">
            <PageImage key={page.src} src={page.src} w={page.w} h={page.h} zoom={zoom} alt={`${rec.name}, page ${pageIx + 1} of ${sample.pages.length}`}>
              {rows.filter((r) => r.box && r.page === pageIx && (r.key === sel || !resolved(r))).map((r) => {
                const [x0, y0, x1, y1] = r.box;
                return (
                  <span key={r.key} ref={r.key === sel ? boxRef : null} className={`box${r.key === sel ? ' is-sel' : ' is-flag'}`} aria-hidden="true"
                    style={{ left: `${(x0 / page.w) * 100}%`, top: `${(y0 / page.h) * 100}%`, width: `${((x1 - x0) / page.w) * 100}%`, height: `${((y1 - y0) / page.h) * 100}%` }} />
                );
              })}
            </PageImage>
          </div>
          <Toolbar aria-label="Page" size="small" className="page-toolbar">
            <Tooltip content="Previous page" relationship="label"><ToolbarButton icon={<ChevronLeftRegular />} disabled={pageIx === 0} onClick={() => setPageIx(pageIx - 1)} /></Tooltip>
            <Caption1 className="nowrap">{`Page ${pageIx + 1} of ${sample.pages.length}`}</Caption1>
            <Tooltip content="Next page" relationship="label"><ToolbarButton icon={<ChevronRightRegular />} disabled={pageIx >= sample.pages.length - 1} onClick={() => setPageIx(pageIx + 1)} /></Tooltip>
            <ToolbarDivider />
            <Tooltip content="Zoom out" relationship="label"><ToolbarButton icon={<ZoomOutRegular />} disabled={zoom <= 1} onClick={() => setZoom(zoom - 0.5)} /></Tooltip>
            <Caption1 className="nowrap">{`${zoom * 100}%`}</Caption1>
            <Tooltip content="Zoom in" relationship="label"><ToolbarButton icon={<ZoomInRegular />} disabled={zoom >= 3} onClick={() => setZoom(zoom + 0.5)} /></Tooltip>
          </Toolbar>
        </section>

        <aside className="values" aria-label="Extracted values">
          {accepted && (
            <div className="seal-band">
              <Picto name="check_mark_button" size={36} />
              <div><Text weight="semibold" block>Approved</Text><Caption1>Values are saved and ready to export.</Caption1></div>
            </div>
          )}
          <div className="ai-note"><SparkleRegular aria-hidden /><Caption1>Read by AI. AI-generated content may be incorrect. Check for accuracy.</Caption1></div>
          {rec.kind === 'scan' && <Caption1 className="muted">Locations aren't marked on photos in this demo. Values come from the digital copy.</Caption1>}

          {!accepted && open.length > 0 && (
            <section className="stack-s" aria-labelledby="tocheck-h">
              <Title as="h2" size="pane" id="tocheck-h">{open.length === 1 ? 'Check this value' : `Check ${open.length} values`}</Title>
              {open.map((r) => (
                <Card key={r.key} className={`check-card${r.key === sel ? ' is-sel' : ''}`} onFocus={() => select(r)} onClick={() => select(r)}>
                  <div className="row between">
                    <Text weight="semibold">{r.label}</Text>
                    <Badge appearance="tint" color="danger" shape="rounded">{r.conf != null ? `${pct(r.conf)} sure` : 'Not found'}</Badge>
                  </div>
                  {r.box && <Snippet page={sample.pages[r.page]} box={r.box} label={r.label} />}
                  <Field hint={r.note || (r.missing ? 'Not found on the document' : undefined)}>
                    <Input value={values[r.key] ?? ''} aria-label={r.label} placeholder={r.missing ? 'Type the value' : ''} onChange={(_, d) => setValue(r.key, d.value)} />
                  </Field>
                  <div className="row">
                    <Button size="small" appearance="primary" icon={<CheckmarkRegular />} onClick={(e) => { e.stopPropagation(); setConfirmed({ ...confirmed, [r.key]: true }); }}>Looks right</Button>
                    {r.box && <Button appearance="transparent" size="small" icon={<EyeRegular />} onClick={(e) => { e.stopPropagation(); select(r); }}>Show on page</Button>}
                  </div>
                </Card>
              ))}
            </section>
          )}

          {!accepted && flagged.length > 0 && open.length === 0 && (
            <div className="ready glow-growth">
              <CheckmarkCircleFilled className="ready-icon pop" fontSize={28} aria-hidden />
              <div><Text weight="semibold" block>All checked</Text><Caption1>Ready to approve when you are.</Caption1></div>
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
                    <div key={r.key} className={r.key === sel ? 'is-sel' : ''} onFocus={() => select(r)} onClick={() => select(r)}>
                      <dt><Caption1 className="muted">{r.label}</Caption1></dt>
                      <dd><Input size="small" appearance="underline" value={values[r.key] ?? ''} aria-label={r.label} onChange={(_, d) => setValue(r.key, d.value)} /></dd>
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
