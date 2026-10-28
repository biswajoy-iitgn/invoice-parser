import { useState } from 'react';
import { Badge, Button, Caption1, Checkbox, MessageBar, MessageBarBody, Tab, TabList, Text, Textarea } from '@fluentui/react-components';
import { CheckmarkRegular } from '@fluentui/react-icons';
import { Title, RHead, RHeader, RBody, RRow, RCell, RTable, count, pct } from '../ui';
import type { ProfileResult, ResultField } from '../types';

const STATUS: Record<ResultField['status'], [label: string, color: 'success' | 'warning' | 'danger' | 'informative']> = {
  verified: ['Found', 'success'], unverified: ['Not found', 'warning'], missing: ['Not printed', 'informative'], invalid: ['Check format', 'danger'],
};

/** Everything the service read from one document: values with where they stand, each table as printed, and the full text. */
export function ResultView({ result, approved, onApprove }: { result: ProfileResult; approved: boolean; onApprove: () => void }) {
  const [tab, setTab] = useState('values');
  const [checked, setChecked] = useState(false);
  // a flagged value is usually also a reason; table and check reasons stand alone
  const open = Math.max(result.fields.filter((f) => f.status !== 'verified' && f.status !== 'missing').length, result.reasons.length);
  const lockedBy = open > 0 && !checked;
  return (
    <div className="stack result-view">
      <div className="stack">
        {result.documentType && <Title as="h2" size="pane">{result.title || result.documentType}</Title>}
        <Caption1 className="muted">{[result.documentType, `${count(result.fields.length, 'value', 'values')}`, `${count(result.tables.length, 'table', 'tables')}`, result.checks.length > 0 && `${result.checks.filter((c) => c.ok === false).length} of ${count(result.checks.length, 'check', 'checks')} failed`, result.models && `read with ${result.models.reader}`].filter(Boolean).join(', ')}</Caption1>
      </div>
      {result.reasons.length > 0 && (
        <MessageBar intent="warning" layout="multiline">
          <MessageBarBody>{result.reasons.join('. ')}</MessageBarBody>
        </MessageBar>
      )}
      <div className="tabs-scroll"><TabList selectedValue={tab} onTabSelect={(_, d) => setTab(String(d.value))}>
        <Tab value="values">Values</Tab>
        <Tab value="tables">Tables</Tab>
        {result.checks.length > 0 && <Tab value="checks">Checks</Tab>}
        <Tab value="text">Text</Tab>
      </TabList></div>

      {tab === 'values' && (
        <div role="tabpanel" aria-label="Values" className="table-card">
          {result.fields.length === 0 ? <Text className="muted">No labelled values were found.</Text> : (
            <RTable layout="auto" aria-label="Values read from the document">
              <RHeader><RRow><RHead>Label</RHead><RHead>Value</RHead><RHead>Status</RHead></RRow></RHeader>
              <RBody>
                {result.fields.map((f, i) => (
                  <RRow key={`${f.label}-${i}`}>
                    <RCell data-label="Label">{f.label}</RCell>
                    <RCell data-label="Value">
                      <div className="cell-stack">
                        <Text>{f.value || 'Not printed'}</Text>
                        {f.modelRead && <Caption1 className="muted">{`The model wrote ${f.modelRead}; the page prints ${f.value}`}</Caption1>}
                        {f.visionRead && f.status !== 'verified' && <Caption1 className="muted">{`Re-read from the image: ${f.visionRead}`}</Caption1>}
                        {f.visionRead && f.status === 'verified' && <Caption1 className="muted">The image re-read agrees</Caption1>}
                        {f.issue && !f.visionRead && <Caption1 className="muted">{f.issue}</Caption1>}
                      </div>
                    </RCell>
                    <RCell data-label="Status">{(() => {
                      // unverified with an issue was found on the page but read with low confidence (handwriting, smudges)
                      const [label, color] = f.status === 'unverified' && f.issue ? ['Low confidence', 'warning' as const] : STATUS[f.status];
                      return <Badge appearance="tint" color={color}>{label}</Badge>;
                    })()}</RCell>
                  </RRow>
                ))}
              </RBody>
            </RTable>
          )}
        </div>
      )}

      {tab === 'tables' && (
        <div role="tabpanel" aria-label="Tables" className="stack">
          {result.tables.length === 0 && <Text className="muted">No tables were found.</Text>}
          {result.tables.map((t, ti) => (
            <div key={ti} className="stack">
              <Caption1 className="muted">{[t.title || `Table ${ti + 1}`, count(t.rows.length, 'row', 'rows'), t.verified !== null && `${pct(t.verified)} of cells found on the page`].filter(Boolean).join(', ')}</Caption1>
              <div className="table-card result-table">
                <RTable layout="auto" size="small" aria-label={t.title || `Table ${ti + 1}`}>
                  <RHeader><RRow>{t.columns.map((c, ci) => <RHead key={ci}>{c || <span className="sr-only">Column {ci + 1}</span>}</RHead>)}</RRow></RHeader>
                  <RBody>
                    {t.rows.map((r, ri) => <RRow key={ri}>{t.columns.map((c, ci) => <RCell key={ci} data-label={c}>{r[ci] ?? ''}</RCell>)}</RRow>)}
                  </RBody>
                </RTable>
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'checks' && (
        <div role="tabpanel" aria-label="Checks" className="table-card">
          <RTable layout="auto" aria-label="Arithmetic checks">
            <RHeader><RRow><RHead>Check</RHead><RHead>Result</RHead></RRow></RHeader>
            <RBody>
              {result.checks.map((c, i) => (
                <RRow key={i}>
                  <RCell data-label="Check"><div className="cell-stack"><Text>{c.name}</Text><Caption1 className="muted">{c.detail}</Caption1></div></RCell>
                  <RCell data-label="Result"><Badge appearance="tint" color={c.ok === null ? 'informative' : c.ok ? 'success' : 'danger'}>{c.ok === null ? 'Not run' : c.ok ? 'Passed' : 'Failed'}</Badge></RCell>
                </RRow>
              ))}
            </RBody>
          </RTable>
        </div>
      )}

      {tab === 'text' && (
        <div role="tabpanel" aria-label="Text" className="stack">
          {result.text.length === 0 && <Text className="muted">No text was kept for this document.</Text>}
          {result.text.map((pg) => (
            <div key={pg.page} className="stack">
              <Caption1 className="muted">{`Page ${pg.page}, ${pg.source === 'ppocr' ? 'read by OCR' : 'from the digital text'}`}</Caption1>
              <Textarea readOnly resize="vertical" rows={12} value={pg.text} aria-label={`Text of page ${pg.page}`} className="result-text" />
            </div>
          ))}
        </div>
      )}

      <div className="stack">
        {open > 0 && !approved && <Checkbox checked={checked} onChange={(_, d) => setChecked(d.checked === true)} label={`I checked the ${count(open, 'flagged item', 'flagged items')} against the document`} />}
        <div className="row">
          <Button appearance="primary" icon={<CheckmarkRegular />} disabled={approved || lockedBy} onClick={onApprove}>{approved ? 'Approved' : 'Approve'}</Button>
          {lockedBy && <Caption1 className="muted">Check the flagged items to approve</Caption1>}
        </div>
      </div>
    </div>
  );
}
