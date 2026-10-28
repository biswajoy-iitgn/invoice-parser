// Entity-relationship diagram for one database: a card per table, parents (referenced tables) to the left, a line from
// each foreign key to the key it references (crow's foot at the many end, bar at the one end). Fluent has no diagram
// control, so the cards are Fluent Cards and only the connectors are SVG. The relationships table under it carries
// the same information without the drawing.
import { useEffect, useRef, useState } from 'react';
import { Card, Caption1, Text, makeStyles, mergeClasses, shorthands, tokens } from '@fluentui/react-components';
import { KeyRegular, LinkRegular, TableRegular } from '@fluentui/react-icons';
import { RHead, RHeader, RBody, RRow, RCell, RTable, Title, count } from './ui';
import { cardAction } from './pages/Home';
import type { TableDef } from './types';

const NARROW = '@media (max-width: 700px)';
const useStyles = makeStyles({
  scroll: { overflowX: 'auto', ...shorthands.padding('4px') },
  canvas: { position: 'relative', display: 'flex', alignItems: 'flex-start', columnGap: '112px', width: 'max-content', ...shorthands.padding('8px'), [NARROW]: { flexDirection: 'column', rowGap: '16px', width: 'auto' } },
  rank: { display: 'flex', flexDirection: 'column', rowGap: '24px', [NARROW]: { width: '100%' } },
  card: { width: '280px', ...shorthands.padding('0'), zIndex: 1, [NARROW]: { width: '100%' } },
  hot: { boxShadow: `0 0 0 2px ${tokens.colorBrandStroke1}, ${tokens.shadow8}` },
  head: { display: 'flex', alignItems: 'center', columnGap: '8px', ...shorthands.padding('10px', '12px'), backgroundColor: tokens.colorNeutralBackground3, ...shorthands.borderBottom('1px', 'solid', tokens.colorNeutralStroke2) },
  name: { flexGrow: 1, fontFamily: tokens.fontFamilyMonospace },
  cols: { listStyleType: 'none', ...shorthands.margin('0'), ...shorthands.padding('4px', '0') },
  col: { display: 'grid', gridTemplateColumns: '20px minmax(0, 1fr) auto', alignItems: 'center', columnGap: '8px', minHeight: '28px', ...shorthands.padding('2px', '12px') },
  colName: { fontFamily: tokens.fontFamilyMonospace, fontSize: tokens.fontSizeBase200, overflowWrap: 'anywhere' },
  ref: { gridColumnStart: 2, gridColumnEnd: 4, color: tokens.colorNeutralForeground3, fontSize: tokens.fontSizeBase100 },
  key: { color: tokens.colorBrandForeground1 },
  svg: { position: 'absolute', insetInlineStart: 0, top: 0, width: '100%', height: '100%', pointerEvents: 'none', overflow: 'visible', [NARROW]: { display: 'none' } },
  line: { fill: 'none', stroke: tokens.colorNeutralStroke1, strokeWidth: '1.5px' },
  lineHot: { stroke: tokens.colorBrandStroke1, strokeWidth: '2.5px' },
});

interface Edge { from: string; col: string; to: string; toCol: string }

export function SchemaDiagram({ tables, onOpen }: { tables: Record<string, TableDef>; onOpen: (name: string) => void }) {
  const s = useStyles();
  const canvas = useRef<HTMLDivElement>(null);
  const [paths, setPaths] = useState<string[]>([]);
  const [hot, setHot] = useState<string | null>(null);
  const names = Object.keys(tables);
  const pk = (n: string) => tables[n].columns.find((c) => c[2] === 'PK')?.[0] ?? '';
  const edges: Edge[] = names.flatMap((n) => tables[n].columns.filter(([, , k]) => k.startsWith('FK ') && tables[k.slice(3)])
    .map(([col, , k]) => ({ from: n, col, to: k.slice(3), toCol: pk(k.slice(3)) })));
  // Column of each table: referenced tables to the left of the tables that reference them.
  const rank: Record<string, number> = {};
  const rankOf = (n: string, seen: string[] = []): number => {
    if (n in rank) return rank[n];
    const refs = edges.filter((e) => e.from === n && e.to !== n && !seen.includes(e.to)).map((e) => rankOf(e.to, [...seen, n]));
    rank[n] = refs.length ? 1 + Math.max(...refs) : 0;
    return rank[n];
  };
  names.forEach((n) => rankOf(n));
  // A table that references nothing sits just left of the first table that references it, so no line crosses a column.
  names.filter((n) => !edges.some((e) => e.from === n)).forEach((n) => {
    const users = edges.filter((e) => e.to === n && e.from !== n).map((e) => rank[e.from]);
    if (users.length) rank[n] = Math.max(0, Math.min(...users) - 1);
  });
  const ranks = [...new Set(Object.values(rank))].sort((a, b) => a - b).map((r) => names.filter((n) => rank[n] === r));
  const key = JSON.stringify(edges);

  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const draw = () => {
      const box = el.getBoundingClientRect();
      const at = (id: string) => el.querySelector(`[data-anchor="${CSS.escape(id)}"]`)?.getBoundingClientRect();
      const list: Edge[] = JSON.parse(key);
      setPaths(list.map((e) => {
        const a = at(`${e.from}.${e.col}`), b = at(`${e.to}.${e.toCol}`);
        if (!a || !b) return '';
        const leftward = a.left > b.left; // child right of parent: leave from its left edge
        const x1 = (leftward ? a.left : a.right) - box.left, y1 = a.top + a.height / 2 - box.top;
        const x2 = (leftward ? b.right : b.left) - box.left, y2 = b.top + b.height / 2 - box.top;
        const d = leftward ? -1 : 1, bend = Math.max(48, Math.abs(x2 - x1) / 2);
        return [
          `M ${x1} ${y1} C ${x1 + d * bend} ${y1}, ${x2 - d * bend} ${y2}, ${x2} ${y2}`,
          `M ${x1 + d * 12} ${y1} L ${x1} ${y1 - 6} M ${x1 + d * 12} ${y1} L ${x1} ${y1} M ${x1 + d * 12} ${y1} L ${x1} ${y1 + 6}`, // many
          `M ${x2 - d * 8} ${y2 - 6} L ${x2 - d * 8} ${y2 + 6}`, // one
        ].join(' ');
      }));
    };
    const ro = new ResizeObserver(draw);
    ro.observe(el);
    return () => ro.disconnect();
  }, [key]);

  return (
    <div className="stack">
      <div className={s.scroll}>
        <div ref={canvas} className={s.canvas}>
          <svg className={s.svg} aria-hidden="true">
            {paths.map((d, i) => d && <path key={i} d={d} className={mergeClasses(s.line, !!hot && (edges[i].from === hot || edges[i].to === hot) && s.lineHot)} />)}
          </svg>
          {ranks.map((group, r) => (
            <div key={r} className={s.rank}>
              {group.map((n) => {
                const t = tables[n];
                return (
                  <Card key={n} className={mergeClasses(s.card, hot === n && s.hot)} {...cardAction(() => onOpen(n), `Open ${n}, ${count(t.columns.length, 'column', 'columns')}, ${count(t.rows.length, 'row', 'rows')}`)}
                    onMouseEnter={() => setHot(n)} onMouseLeave={() => setHot(null)} onFocus={() => setHot(n)} onBlur={() => setHot(null)}>
                    <div className={s.head}><TableRegular aria-hidden /><Text weight="semibold" className={s.name}>{n}</Text><Caption1>{count(t.rows.length, 'row', 'rows')}</Caption1></div>
                    <ul className={s.cols}>
                      {t.columns.map(([c, type, k]) => (
                        <li key={c} className={s.col} data-anchor={`${n}.${c}`}>
                          {k === 'PK' ? <KeyRegular className={s.key} aria-label="Key" /> : k.startsWith('FK ') ? <LinkRegular aria-label="Reference" /> : <span />}
                          <Text className={s.colName}>{c}</Text>
                          <Caption1 className="muted">{type}</Caption1>
                          {k.startsWith('FK ') && <span className={s.ref}>{`References ${k.slice(3)}`}</span>}
                        </li>
                      ))}
                    </ul>
                  </Card>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <Title as="h2" size="section">Relationships</Title>
      {edges.length ? (
        <div className="table-card">
          <RTable layout="auto" aria-label="Relationships">
            <RHeader><RRow><RHead>From</RHead><RHead>To</RHead><RHead>Kind</RHead></RRow></RHeader>
            <RBody>
              {edges.map((e) => (
                <RRow key={`${e.from}.${e.col}`}>
                  <RCell data-label="From"><Text font="monospace">{`${e.from}.${e.col}`}</Text></RCell>
                  <RCell data-label="To"><Text font="monospace">{`${e.to}.${e.toCol}`}</Text></RCell>
                  <RCell data-label="Kind">{`Many ${e.from} rows to one ${e.to} row`}</RCell>
                </RRow>
              ))}
            </RBody>
          </RTable>
        </div>
      ) : <Caption1 className="muted">No table in this database references another.</Caption1>}
    </div>
  );
}
