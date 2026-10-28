import { useState } from 'react';
import type { ReactNode } from 'react';
import {
  Button, TabList, Tab, Card, Text, Caption1, List, ListItem, Spinner, Skeleton, SkeletonItem, ProgressBar, Field, Link,
} from '@fluentui/react-components';
import { VerticalStackedBarChart, LineChart, HorizontalBarChartWithAxis, DonutChart } from '@fluentui/react-charts';
import { ArrowClockwiseRegular, ChevronRightRegular, WarningFilled, TableRegular, ChartMultipleRegular } from '@fluentui/react-icons';
import { useApp, PageHeader, Section, num, money, useUrlState, RHead, RHeader, RBody, RRow, RCell, RTable, CellLink } from '../ui';
import { HISTORY, CHARGE_KEYS, DAYS, FIELD_LABELS } from '../data';
import { viz } from '../theme';
import { ENGINES } from '../engines';
import type { DocRecord } from '../types';
import { useControlStyles } from '../styles';

const label = FIELD_LABELS;
const short = (vendor: string) => vendor.replace(/,? (Pvt\.|Private) (Ltd\.?|Limited)$/i, '');
const lakh = (n: number) => +(n / 1e5).toFixed(1);
const median = (xs: number[]) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : 0; };
const tally = <K extends string>(keys: K[]): [K, number][] =>
  Object.entries(keys.reduce<Record<string, number>>((m, k) => ({ ...m, [k]: (m[k] || 0) + 1 }), {})) as [K, number][];
type Tab = 'overview' | 'spend' | 'accuracy' | 'engines';
interface Anomaly { title: string; detail: string; doc: string }

// A figure and its label, nothing else (Power BI card)
function Kpi({ value, label: text, hint }: { value: ReactNode; label: string; hint?: string }) {
  return (
    <Card className="kpi">
      <span className="big-number">{value}</span><Caption1 block>{text}</Caption1>{hint && <Caption1 block className="muted">{hint}</Caption1>}
    </Card>
  );
}

// Every chart can show its numbers as a table (WCAG 1.1.1 and 1.4.11: the data never depends on seeing the colours).
function ChartCard({ title, head, rows, children }: { title: string; head: string[]; rows: (string | number)[][]; children: ReactNode }) {
  const [table, setTable] = useState(false);
  return (
    <Section title={title} action={
      <Button size="small" appearance="subtle" icon={table ? <ChartMultipleRegular /> : <TableRegular />} onClick={() => setTable(!table)}>{table ? 'Show as chart' : 'Show as table'}</Button>
    }>
      {table ? (
        <RTable size="small" aria-label={title} className="chart-table">
          <RHeader><RRow>{head.map((h, i) => <RHead key={h} className={i ? 'is-num' : ''}>{h}</RHead>)}</RRow></RHeader>
          <RBody>{rows.map((r) => <RRow key={r[0]}>{r.map((c, i) => <RCell key={i} className={i ? 'is-num' : ''}>{c}</RCell>)}</RRow>)}</RBody>
        </RTable>
      ) : children}
    </Section>
  );
}

export default function Insights() {
  const { role, notify, go, allDocs, docRecord, profiles, dark, flagBelow, edits } = useApp();
  const [tab, setTab] = useUrlState<Tab>('tab', 'overview');
  const [updated, setUpdated] = useState(HISTORY.updated);
  const [refreshing, setRefreshing] = useState(false);
  const C = viz(dark);
  const cs = useControlStyles();
  const legend = { styles: { text: cs.legendText } };

  // One source: the documents in the workspace (today's batches and the archived history).
  const recs = allDocs.filter((d) => d.day && DAYS.includes(d.day)).map((d) => docRecord(d.id)).filter((r): r is DocRecord => !!r);
  const decided = recs.filter((r) => ['accepted', 'exported'].includes(r.status));
  // A document counts as changed when the AI's value for any field was corrected, before or after this visit.
  const corrected = (r: DocRecord) => !!r.edited || Object.entries(edits[r.id] || {}).some(([k, v]) => v !== (r.sample?.fields.find((f) => f.key === k)?.value ?? ''));
  const acceptRate = (rs: DocRecord[]) => (rs.length ? Math.round((100 * rs.filter((r) => !corrected(r)).length) / rs.length) : null);
  const volume = profiles.map((p) => ({ name: p.name, data: DAYS.map((day) => recs.filter((r) => r.day === day && r.profile === p.id).length) }));
  const dayMax = Math.max(1, ...DAYS.map((_, i) => volume.reduce((a, s) => a + s.data[i], 0)));
  const yTop = Math.ceil(dayMax / 5) * 5;
  const trend = DAYS.map((day) => ({ day, rate: acceptRate(decided.filter((r) => r.day === day)) })).filter((p): p is { day: string; rate: number } => p.rate !== null);

  const freight = recs.filter((r) => r.profile === 'freight' && r.values.gross_total);
  const byVendor = Object.entries(freight.reduce<Record<string, number>>((m, r) => ({ ...m, [r.values.vendor]: (m[r.values.vendor] || 0) + num(r.values.gross_total) }), {}))
    .sort((a, b) => b[1] - a[1]);
  const charges = CHARGE_KEYS.map((k): [string, number] => [label[k], freight.reduce((a, r) => a + num(r.values[k]), 0)]).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const parts: [string, number][] = [...charges.slice(0, 4), ...(charges.length > 4 ? [['Everything else', charges.slice(4).reduce((a, [, v]) => a + v, 0)] as [string, number]] : [])];
  const flaggedKeys = recs.flatMap((r) => (r.sample ? r.sample.fields.filter((f) => f.conf < flagBelow).map((f) => f.key) : r.flagged || []));
  const flagged = tally(flaggedKeys).sort((a, b) => b[1] - a[1]).slice(0, 6);

  const seen: Record<string, DocRecord> = {};
  const anomalies: Anomaly[] = [];
  recs.forEach((r) => {
    const inv = r.values.invoice_no;
    if (inv && seen[inv]) anomalies.push({ title: 'Possible duplicate', detail: `${seen[inv].name} and ${r.name} have the same invoice number ${inv}`, doc: r.id });
    else if (inv) seen[inv] = r;
  });
  freight.forEach((r) => {
    const sum = CHARGE_KEYS.reduce((a, k) => a + num(r.values[k]), 0);
    const net = num(r.values.total);
    if (net && Math.abs(sum - net) > 1) anomalies.push({ title: 'Charges don\'t add up', detail: `${r.name}: read charges total ${money(sum)}, net total is ${money(net)}`, doc: r.id });
  });

  const refresh = () => {
    setRefreshing(true);
    setTimeout(() => { setRefreshing(false); setUpdated('24 Oct, just now'); notify('Insights refreshed'); }, 1200);
  };

  return (
    <div className="page">
      <PageHeader title="Insights" description={`Last 10 days, updated ${updated}`}>
        {role === 'admin' && <Button icon={refreshing ? <Spinner size="tiny" /> : <ArrowClockwiseRegular />} disabled={refreshing} onClick={refresh}>{refreshing ? 'Refreshing …' : 'Refresh now'}</Button>}
      </PageHeader>

      <div className="tabs-scroll"><TabList selectedValue={tab} onTabSelect={(_, d) => setTab(d.value as Tab)}>
        <Tab value="overview">Overview</Tab>
        <Tab value="spend">Spending</Tab>
        <Tab value="accuracy">Accuracy</Tab>
        {role === 'admin' && <Tab value="engines">Engines and cost</Tab>}
      </TabList></div>

      {refreshing && (
        <Skeleton aria-label={'Refreshing insights …'} className="stack">
          <div className="kpis">{[0, 1, 2].map((i) => <SkeletonItem key={i} size={72} />)}</div>
          <SkeletonItem className="skeleton-chart" />
        </Skeleton>
      )}
      {!refreshing && tab === 'overview' && (
        <div role="tabpanel" aria-label="Overview" className="stack">
          <div className="kpis">
            <Kpi value={recs.length} label="Documents read" />
            <Kpi value={`${acceptRate(decided)}%`} label="Approved without corrections" hint="Approved documents where no AI value was changed" />
            <Kpi value={`${median(recs.map((r) => r.seconds ?? 0).filter(Boolean))} s`} label="Median time per document" />
          </div>
          <ChartCard title="Documents per day" head={['Day', ...volume.map((s) => s.name)]} rows={DAYS.map((day, i) => [day, ...volume.map((s) => s.data[i])])}>
            <VerticalStackedBarChart
              legendProps={legend}
              height={300}
              barWidth="auto"
              maxBarWidth={40}
              barGapMax={2}
              data={DAYS.map((d, i) => ({ xAxisPoint: d, chartData: volume.map((s, k) => ({ legend: s.name, data: s.data[i], color: C.series[k] })) }))}
              yAxisTitle="Documents"
              yMaxValue={yTop}
              yAxisTickValues={Array.from({ length: yTop / 5 + 1 }, (_, i) => i * 5)}
            />
          </ChartCard>
          <Section title="Worth a look" action={anomalies.length > 5 && <Link onClick={() => go('library')}>{`${anomalies.length - 5} more`}</Link>}>
            <List navigationMode="items" aria-label="Things worth a look">
              {anomalies.slice(0, 5).map((a) => (
                <ListItem key={a.detail} value={a.detail} className={cs.insightItem} onAction={() => go('review', a.doc)} aria-label={`${a.title}. ${a.detail}`}>
                  <WarningFilled className="warn-text" fontSize={20} aria-hidden />
                  <span className="list-main"><Text weight="semibold" block>{a.title}</Text><Caption1 block>{a.detail}</Caption1></span>
                  <ChevronRightRegular className="flip-rtl" aria-hidden />
                </ListItem>
              ))}
            </List>
          </Section>
        </div>
      )}

      {!refreshing && tab === 'spend' && (
        <div role="tabpanel" aria-label="Spending" className="grid-2">
          <ChartCard title="Freight spend by vendor" head={['Vendor', 'Gross, INR lakh']} rows={byVendor.map(([v, x]) => [short(v), lakh(x)])}>
            <HorizontalBarChartWithAxis height={300} hideLegend showYAxisLables margins={{ left: 200 }} data={byVendor.map(([y, x]) => ({ x: lakh(x), y: short(y), legend: short(y), color: C.one }))} xAxisTitle="Gross, INR lakh" />
          </ChartCard>
          <ChartCard title="Where the money goes" head={['Charge', 'INR lakh']} rows={parts.map(([l, v]) => [l, lakh(v)])}>
            <DonutChart legendProps={legend} height={300} innerRadius={64} valueInsideDonut={`${lakh(charges.reduce((a, [, v]) => a + v, 0))} lakh`} data={{ chartData: parts.map(([legend, data], k) => ({ legend, data: Math.round(data), color: C.parts[k] })) }} />
          </ChartCard>
        </div>
      )}

      {!refreshing && tab === 'accuracy' && (
        <div role="tabpanel" aria-label="Accuracy" className="grid-2">
          <ChartCard title="Approved without corrections" head={['Day', 'Approved without corrections']} rows={trend.map((p) => [p.day, `${p.rate}%`])}>
            <LineChart
              height={280}
              data={{ chartTitle: 'Approved without corrections', lineChartData: [{ legend: 'Approved without corrections', color: C.one, data: trend.map((p) => ({ x: new Date(`${p.day} 2026`), y: p.rate })) }] }}
              yMinValue={50} yMaxValue={100}
              yAxisTickFormat={(v: number) => `${v}%`}
              hideLegend
            />
          </ChartCard>
          <ChartCard title="Fields the AI is least sure about" head={['Field', 'Documents flagged']} rows={flagged.map(([k, x]) => [label[k], x])}>
            <HorizontalBarChartWithAxis height={280} hideLegend showYAxisLables margins={{ left: 160 }} xAxis={{ tick0: 0, tickStep: 1 }} data={flagged.map(([k, x]) => ({ x, y: label[k], legend: label[k], color: C.one }))} xAxisTitle="Documents flagged" />
          </ChartCard>
        </div>
      )}

      {!refreshing && tab === 'engines' && role === 'admin' && (
        <div role="tabpanel" aria-label="Engines and cost" className="grid-2">
          <Section title="Engine health, last 7 days">
            <RTable aria-label="Engine health, last 7 days" layout="auto">
              <RHeader><RRow><RHead>Engine</RHead><RHead>Median time</RHead><RHead>Failures</RHead></RRow></RHeader>
              <RBody>
                {HISTORY.engines.map((e) => (
                  <RRow key={e.name}>
                    <RCell data-label="Engine"><CellLink appearance="default" onClick={() => go('engines', ENGINES.find((x) => x.name === e.name)?.id)}>{e.name}</CellLink></RCell>
                    <RCell data-label="Median time">{e.latency}</RCell>
                    <RCell data-label="Failures">{e.failures}</RCell>
                  </RRow>
                ))}
              </RBody>
            </RTable>
          </Section>
          <Section title="API spend this month">
            {HISTORY.budgets.map((b) => (
              <Field key={b.name} label={b.name} validationState={b.spent / b.cap >= 0.8 ? 'warning' : 'none'} validationMessage={`USD ${b.spent} of ${b.cap}`}>
                <ProgressBar value={b.spent / b.cap} color={b.spent / b.cap >= 0.8 ? 'warning' : 'brand'} thickness="large" />
              </Field>
            ))}
            <Link onClick={() => go('engines', 'evaluator')}>Change the Claude API budget</Link>
            <Link onClick={() => go('engines', 'ocr')}>Change the Azure Vision budget</Link>
          </Section>
        </div>
      )}
    </div>
  );
}
