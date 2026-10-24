import { useState } from 'react';
import {
  Button, TabList, Tab, Card, Text, Caption1, List, ListItem, Spinner, Skeleton, SkeletonItem, ProgressBar, Field, Table, TableHeader, TableRow, TableHeaderCell, TableBody, TableCell, Link,
} from '@fluentui/react-components';
import { VerticalStackedBarChart, LineChart, HorizontalBarChartWithAxis, DonutChart } from '@fluentui/react-charts';
import { ArrowClockwiseRegular, ChevronRightRegular, WarningFilled } from '@fluentui/react-icons';
import { useApp, PageHeader, Section, num, money, Picto } from '../ui';
import { HISTORY, CHARGE_KEYS, FIELD_LIBRARY, DOCS, FLAG_BELOW } from '../data';
import { S } from '../theme';

const SKY = [S.blue[800], S.blue[600], S.blue[400], S.blue[300], S.blue[200], S.grey[400]];
const label = Object.fromEntries(FIELD_LIBRARY.invoice.map(([k, l]) => [k, l]));

function Kpi({ picto, value, label: text, onClick }) {
  return (
    <Card className="kpi" orientation="horizontal" as={onClick ? 'button' : 'div'} onClick={onClick}>
      <Picto name={picto} size={48} />
      <div><span className="big-number">{value}</span><Caption1 block>{text}</Caption1></div>
      {onClick && <ChevronRightRegular aria-hidden />}
    </Card>
  );
}

export default function Insights() {
  const { role, notify, go, allDocs, docRecord } = useApp();
  const [tab, setTab] = useState('overview');
  const [updated, setUpdated] = useState(HISTORY.updated);
  const [refreshing, setRefreshing] = useState(false);

  const recs = allDocs.filter((d) => DOCS[d.sample || d.id]).map((d) => docRecord(d.id));
  const digital = recs.filter((r) => r.kind === 'vector');
  const total = HISTORY.volume.reduce((a, s) => a + s.data.reduce((x, y) => x + y, 0), 0);
  const avgAccept = Math.round(HISTORY.autoAccept.reduce((a, p) => a + p.y, 0) / HISTORY.autoAccept.length);

  // Business insights from the documents' own values.
  const byVendor = Object.entries(digital.reduce((m, r) => ({ ...m, [r.sample.vendor]: (m[r.sample.vendor] || 0) + num(r.values.gross_total) }), {}))
    .sort((a, b) => b[1] - a[1]);
  const charges = CHARGE_KEYS.map((k) => [label[k], digital.reduce((a, r) => a + num(r.values[k]), 0)]).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const topCharges = [...charges.slice(0, 5), ...(charges.length > 5 ? [['Everything else', charges.slice(5).reduce((a, [, v]) => a + v, 0)]] : [])];
  const flagged = Object.entries(recs.flatMap((r) => r.sample.fields.filter((f) => f.conf < FLAG_BELOW).map((f) => f.key))
    .reduce((m, k) => ({ ...m, [k]: (m[k] || 0) + 1 }), {})).sort((a, b) => b[1] - a[1]).slice(0, 6);

  const seen = {};
  const anomalies = [];
  recs.forEach((r) => {
    const inv = r.values.invoice_no;
    if (inv && seen[inv]) anomalies.push({ title: 'Possible duplicate', detail: `${seen[inv].name} and ${r.name} have the same invoice number ${inv}`, doc: r.id });
    else if (inv) seen[inv] = r;
  });
  digital.forEach((r) => {
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
      <PageHeader picto="chart_increasing" title="Insights" description={`Updated ${updated}`}>
        {role === 'admin' && <Button icon={refreshing ? <Spinner size="tiny" /> : <ArrowClockwiseRegular />} disabled={refreshing} onClick={refresh}>{refreshing ? 'Refreshing\u00a0…' : 'Refresh now'}</Button>}
      </PageHeader>

      <div className="tabs-scroll"><TabList selectedValue={tab} onTabSelect={(_, d) => setTab(d.value)}>
        <Tab value="overview">Overview</Tab>
        <Tab value="spend">Spending</Tab>
        <Tab value="accuracy">Accuracy</Tab>
        {role === 'admin' && <Tab value="engines">Engines and cost</Tab>}
      </TabList></div>

      {refreshing && (
        <Skeleton aria-label={'Refreshing insights\u00a0…'} className="stack">
          <div className="kpis">{[0, 1, 2].map((i) => <SkeletonItem key={i} size={72} />)}</div>
          <SkeletonItem size={300} />
        </Skeleton>
      )}
      {!refreshing && tab === 'overview' && (
        <div role="tabpanel" aria-label="Overview" className="stack">
          <div className="kpis">
            <Kpi picto="bar_chart" value={total} label="Documents in the last 10 days" />
            <Kpi picto="check_mark_button" value={`${avgAccept}%`} label="Accepted without changes" />
            <Kpi picto="stopwatch" value={`${HISTORY.medianSeconds} s`} label="Median time per document" />
          </div>
          <Section title="Documents per day">
            <VerticalStackedBarChart
              height={300}
              barWidth="auto"
              maxBarWidth={40}
              data={HISTORY.days.map((d, i) => ({ xAxisPoint: d, chartData: HISTORY.volume.map((s, k) => ({ legend: s.name, data: s.data[i], color: SKY[k] })) }))}
              yAxisTitle="Documents"
            />
          </Section>
          <Section title="Worth a look" icon={<Picto name="light_bulb" size={32} />} action={anomalies.length > 5 && <Link onClick={() => go('library')}>{`${anomalies.length - 5} more`}</Link>}>
            <List navigationMode="items" className="insight-list" aria-label="Things worth a look">
              {anomalies.slice(0, 5).map((a) => (
                <ListItem key={a.detail} value={a.detail} onAction={() => go('review', a.doc)} aria-label={`${a.title}. ${a.detail}`}>
                  <WarningFilled className="warn-text" fontSize={20} aria-hidden />
                  <span className="list-main"><Text weight="semibold" block>{a.title}</Text><Caption1 block>{a.detail}</Caption1></span>
                  <ChevronRightRegular aria-hidden />
                </ListItem>
              ))}
            </List>
          </Section>
        </div>
      )}

      {!refreshing && tab === 'spend' && (
        <div role="tabpanel" aria-label="Spending" className="grid-2">
          <Section title="Freight spend by vendor" icon={<Picto name="money_bag" size={32} />}>
            <HorizontalBarChartWithAxis height={300} hideLegend data={byVendor.map(([y, x]) => ({ x: Math.round(x), y, legend: y, color: S.blue[700] }))} xAxisTitle="INR, gross" />
          </Section>
          <Section title="Where the money goes">
            <DonutChart height={300} innerRadius={64} data={{ chartTitle: 'Charge mix', chartData: topCharges.map(([legend, data], k) => ({ legend, data: Math.round(data), color: SKY[k] })) }} />
          </Section>
        </div>
      )}

      {!refreshing && tab === 'accuracy' && (
        <div role="tabpanel" aria-label="Accuracy" className="grid-2">
          <Section title="Accepted without changes">
            <LineChart
              height={280}
              data={{ chartTitle: 'Accepted without changes', lineChartData: [{ legend: 'Accepted without changes, %', color: S.blue[600], data: HISTORY.autoAccept.map((p) => ({ x: new Date(`${p.x} 2026`), y: p.y })) }] }}
              yMinValue={80} yMaxValue={100}
            />
          </Section>
          <Section title="Fields the AI is least sure about">
            <HorizontalBarChartWithAxis height={280} hideLegend data={flagged.map(([k, x]) => ({ x, y: label[k], legend: label[k], color: S.blue[600] }))} xAxisTitle="Documents flagged" />
          </Section>
        </div>
      )}

      {!refreshing && tab === 'engines' && role === 'admin' && (
        <div role="tabpanel" aria-label="Engines and cost" className="grid-2">
          <div className="table-card">
            <Table aria-label="Engine health, last 7 days" className="responsive">
              <TableHeader><TableRow><TableHeaderCell>Engine</TableHeaderCell><TableHeaderCell>Median time</TableHeaderCell><TableHeaderCell>Failures</TableHeaderCell></TableRow></TableHeader>
              <TableBody>
                {HISTORY.engines.map((e) => (
                  <TableRow key={e.name}>
                    <TableCell data-label="Engine"><Link onClick={() => go('engines')}>{e.name}</Link></TableCell>
                    <TableCell data-label="Median time">{e.latency}</TableCell>
                    <TableCell data-label="Failures">{e.failures}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <Section title="API spend this month">
            {HISTORY.budgets.map((b) => (
              <Field key={b.name} label={b.name} validationState={b.spent / b.cap >= 0.8 ? 'warning' : 'none'} validationMessage={`USD ${b.spent} of ${b.cap}`}>
                <ProgressBar value={b.spent / b.cap} color={b.spent / b.cap >= 0.8 ? 'warning' : 'brand'} thickness="large" />
              </Field>
            ))}
            <Link onClick={() => go('engines')}>Change budgets</Link>
          </Section>
        </div>
      )}
    </div>
  );
}
