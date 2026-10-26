import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentType } from 'react';
import {
  Avatar, Button, Menu, MenuTrigger, MenuPopover, MenuList, MenuItemRadio, MenuGroup, MenuGroupHeader, MenuDivider,
  Toaster, Toast, ToastTitle, ToastBody, useToastController, useId, Tooltip,
  List, ListItem, SearchBox, Dropdown, Option, OverlayDrawer, DrawerHeader, DrawerHeaderTitle, DrawerBody, Body1, Caption1, Persona, TabList, Tab,
  FluentProvider, MessageBar, MessageBarBody, MessageBarActions, Spinner, MenuItem, mergeClasses,
} from '@fluentui/react-components';
import {
  bundleIcon, HomeFilled, HomeRegular, DocumentSearchFilled, DocumentSearchRegular, LibraryFilled, LibraryRegular, DocumentTextExtractFilled, DocumentTextExtractRegular,
  DataTrendingFilled, DataTrendingRegular, SettingsFilled, SettingsRegular, DatabaseFilled, FlowFilled,
  AppsListDetailRegular, FlowRegular, ServerRegular, DatabaseRegular, PeopleRegular, ShieldTaskRegular, ArrowSyncCircleRegular,
  AlertRegular, DismissRegular, SignOutRegular, MoreHorizontalFilled, MoreHorizontalRegular, CheckmarkCircleRegular, LightbulbRegular, PlugDisconnectedRegular,
} from '@fluentui/react-icons';
import type { FluentIcon } from '@fluentui/react-icons';
import { AppCtx, EmptyState, IconWithCount, Title, Glyph, PageBoundary, readParam, useMedia, count } from './ui';
import { useShellStyles, useStaticStyles } from './styles';
import { theme, darkTheme } from './theme';
import { PROFILES, RUNS, TABLES, WORKFLOWS, PATHS, NOTIFICATIONS, DOCS, USERS, AUDIT, CONNECTIONS, meanConf } from './data';
import { ENGINE_DEFAULTS } from './engines';
import type { EngineState } from './engines';
import { chain, hashOf, stamp } from './audit';
import { HelpMenu } from './help';
import type { HelpDialog } from './help';
import type {
  AppContextValue, AuditEntry, Connection, Doc, DocRecord, Intent, Note, NoteKind, Profile, Role, Route, Run, RunDoc, Tables, UploadItem, Values, Workflow,
} from './types';
import Home from './pages/Home';

// Every page but Home loads on first visit, so the charts and settings code stay out of the first download.
const named = <M extends Record<string, unknown>>(load: () => Promise<M>, name: keyof M) =>
  lazy(() => load().then((m) => ({ default: m[name] as ComponentType })));
const Extract = lazy(() => import('./pages/Extract'));
const Review = lazy(() => import('./pages/Review'));
const Library = lazy(() => import('./pages/Library'));
const Insights = lazy(() => import('./pages/Insights'));
const Data = lazy(() => import('./pages/Data'));
const Workflows = lazy(() => import('./pages/Workflows'));
const Engines = lazy(() => import('./pages/Engines'));
const RunDetail = named(() => import('./pages/Runs'), 'RunDetail');
const Profiles = named(() => import('./pages/Profiles'), 'Profiles');
const ProfileEditor = named(() => import('./pages/Profiles'), 'ProfileEditor');
const Users = named(() => import('./pages/Admin'), 'Users');
const Audit = named(() => import('./pages/Admin'), 'Audit');
const Updates = named(() => import('./pages/Admin'), 'Updates');

// Wait UX: nothing for short loads, a labelled spinner after 400 ms.
function PageLoading() {
  const [show, setShow] = useState(false);
  useEffect(() => { const t = setTimeout(() => setShow(true), 400); return () => clearTimeout(t); }, []);
  return show ? <div className="page-loading" role="status"><Spinner label={'Loading\u00a0…'} /></div> : null;
}
// Work is kept in this browser until a server takes over: runs, edits, profiles, tables, automations, notifications.
// A version bump drops data saved under an older shape instead of misreading it.
// Sign-out clears it: business data never outlives the session in a shared browser.
const SAVE_KEY = 'dx-workspace-v2';
interface Saved {
  profiles?: Profile[]; runs?: Run[]; tables?: Tables; workflows?: Workflow[]; edits?: Record<string, Values>; notes?: Note[]; unread?: number; seen?: string[];
  engines?: EngineState; audit?: AuditEntry[]; connections?: Connection[]; samples?: boolean;
}
const saved: Saved = (() => { try { return JSON.parse(localStorage.getItem(SAVE_KEY) || '{}') as Saved; } catch { return {}; } })();
type Appearance = 'system' | 'light' | 'dark';
const stored = (k: string, fallback: Appearance): Appearance => { try { return (localStorage.getItem(k) as Appearance | null) || fallback; } catch { return fallback; } };
const store = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* private mode: keep it for this visit */ } };

// Teams-style app rail: the places people work, role-based; system settings at the bottom.
// [route, label, icon, admin only]
type RailItem = [route: string, label: string, icon: FluentIcon, adminOnly: boolean];
const RAIL: RailItem[] = [
  ['home', 'Home', bundleIcon(HomeFilled, HomeRegular), false],
  ['extract', 'Extract', bundleIcon(DocumentTextExtractFilled, DocumentTextExtractRegular), false],
  ['review', 'Review', bundleIcon(DocumentSearchFilled, DocumentSearchRegular), false],
  ['library', 'Documents', bundleIcon(LibraryFilled, LibraryRegular), false],
  ['data', 'Databases', bundleIcon(DatabaseFilled, DatabaseRegular), false],
  ['workflows', 'Automations', bundleIcon(FlowFilled, FlowRegular), true],
  ['insights', 'Insights', bundleIcon(DataTrendingFilled, DataTrendingRegular), false],
];
const SettingsIcon = bundleIcon(SettingsFilled, SettingsRegular);
const MoreIcon = bundleIcon(MoreHorizontalFilled, MoreHorizontalRegular);
const PHONE_TABS = new Set(['home', 'extract', 'review', 'library']); // the rest sit under More on phones
type SettingsItem = [route: string, label: string, icon: FluentIcon];
const SETTINGS: SettingsItem[] = [
  ['profiles', 'Profiles', AppsListDetailRegular],
  ['engines', 'AI and engines', ServerRegular],
  ['users', 'People', PeopleRegular],
  ['audit', 'Audit log', ShieldTaskRegular],
  ['updates', 'Updates', ArrowSyncCircleRegular],
];
const ADMIN_ONLY = new Set(['workflows', 'profile', ...SETTINGS.map(([k]) => k)]);
const PAGES: Record<string, ComponentType> = {
  home: Home, extract: Extract, run: RunDetail, review: Review, library: Library, insights: Insights, data: Data,
  profiles: Profiles, profile: ProfileEditor, workflows: Workflows, engines: Engines, users: Users, audit: Audit, updates: Updates,
};
const ALIAS: Record<string, string> = { runs: 'extract', upload: 'extract', settings: 'profiles', automations: 'workflows', databases: 'data' };
const SETTINGS_OF: Record<string, string> = { profile: 'profiles' };
const NOTE_ICON: Record<NoteKind, [FluentIcon, string]> = { approval: [CheckmarkCircleRegular, 'is-ok'], run: [DocumentTextExtractRegular, ''], insight: [LightbulbRegular, ''], engine: [PlugDisconnectedRegular, 'is-bad'] };
const ME = USERS[0]; // the signed-in person; a server session supplies this
const LANGS = [['en', 'English'], ['gu', 'ગુજરાતી'], ['hi', 'हिन्दी']];

// Mock reader: a file whose name matches a bundled sample opens that sample's real pages and values.
function matchSample(name: string): string | undefined {
  const n = name.toLowerCase().replace(/[_\s-]+/g, ' ');
  const hit = Object.values(DOCS).find((d) => {
    const base = d.name.toLowerCase().replace(/\.\w+$/, '').replace(/[_\s-]+/g, ' ').replace(' phone', '');
    return n.includes(base) && d.kind === (/(phone|jpg|jpeg|png|heic)/.test(n) ? 'scan' : 'vector');
  });
  return hit?.id;
}

// Hash routes (#/review/doc-dachser) so Back works and pages can be linked.
const parse = (): Route => {
  const [p, id] = location.hash.replace(/^#\/?/, '').split('?')[0].split('/');
  return { page: ALIAS[p] || p || 'home', id: id ? decodeURIComponent(id) : undefined };
};

export default function App() {
  useStaticStyles();
  const ss = useShellStyles();
  const [page, setPage] = useState(parse);
  const [role, setRole] = useState<Role>('admin'); // what the signed-in sysadmin is viewing as (Power BI "View as")
  const [appearance, setAppearance] = useState<Appearance>(() => stored('appearance', 'system'));
  const osDark = useMedia('(prefers-color-scheme: dark)');
  const dark = appearance === 'dark' || (appearance === 'system' && osDark);
  const phone = useMedia('(max-width: 640px)');
  // English, Gujarati and Hindi all read left to right; in development, ?dir=rtl previews the mirrored layout.
  const dir = import.meta.env.DEV && new URLSearchParams(location.search).get('dir') === 'rtl' ? 'rtl' : 'ltr';
  const [signedIn, setSignedIn] = useState(true);
  const [profiles, setProfiles] = useState<Profile[]>(saved.profiles || PROFILES);
  const [runs, setRuns] = useState<Run[]>(saved.runs || RUNS);
  const [tables, setTables] = useState<Tables>(saved.tables || TABLES);
  const [workflows, setWorkflows] = useState<Workflow[]>(saved.workflows || WORKFLOWS);
  const [edits, setEdits] = useState<Record<string, Values>>(saved.edits || {}); // docId -> { fieldKey: value }
  const [notes, setNotes] = useState<Note[]>(saved.notes || NOTIFICATIONS);
  const [unread, setUnread] = useState<number>(saved.unread ?? 3);
  const [notesOpen, setNotesOpen] = useState(false);
  const [seen, setSeen] = useState<Set<string>>(() => new Set(saved.seen || [])); // notifications read in an earlier visit to the panel
  const [query, setQuery] = useState(() => readParam('q') || '');
  const [engines, setEngines] = useState<EngineState>(saved.engines || ENGINE_DEFAULTS);
  const [engineDraft, setEngineDraft] = useState<EngineState | null>(null);
  const [connections, setConnections] = useState<Connection[]>(saved.connections || CONNECTIONS);
  const [audit, setAudit] = useState<AuditEntry[]>(saved.audit || AUDIT);
  const [samples, setSamples] = useState(saved.samples ?? true);
  const [help, setHelp] = useState<HelpDialog | null>(null);
  const warnedFull = useRef(false);
  const toasterId = useId('toaster');
  const { dispatchToast } = useToastController(toasterId);
  useEffect(() => {
    // a batch still reading can't resume after a reload, so only finished batches are kept
    const keep: Saved = { profiles, runs: runs.filter((r) => r.status !== 'running'), tables, workflows, edits, notes, unread, seen: [...seen], engines, audit, connections, samples };
    try { localStorage.setItem(SAVE_KEY, JSON.stringify(keep)); warnedFull.current = false; } catch {
      if (!warnedFull.current) dispatchToast(<Toast><ToastTitle>{"Changes aren't being kept"}</ToastTitle><ToastBody>This browser is out of space. Recent changes are lost when you close the app.</ToastBody></Toast>, { intent: 'warning', timeout: -1 });
      warnedFull.current = true;
    }
  }, [profiles, runs, tables, workflows, edits, notes, unread, seen, engines, audit, connections, samples, dispatchToast]);
  // Audit entries are appended one at a time, each hashed onto the one before (src/audit.ts).
  const auditRef = useRef(audit);
  const auditQueue = useRef<Promise<void>>(Promise.resolve());
  useEffect(() => {
    if (!auditRef.current.some((e) => !e.hash)) return;
    auditQueue.current = auditQueue.current.then(async () => { auditRef.current = await chain(auditRef.current); setAudit(auditRef.current); });
  }, []);
  const log = (action: string, target: string) => {
    const e = { at: stamp(), actor: ME.name, action, target };
    auditQueue.current = auditQueue.current.then(async () => {
      auditRef.current = [{ ...e, hash: await hashOf(auditRef.current[0]?.hash ?? '', e) }, ...auditRef.current];
      setAudit(auditRef.current);
    });
  };
  // Microsoft 365 shortcuts: Alt+Q goes to search; Shift+? lists the shortcuts.
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && (e.target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName));
      if (e.altKey && e.code === 'KeyQ') { e.preventDefault(); document.querySelector<HTMLInputElement>('.appbar-search input')?.focus(); }
      else if (e.key === '?' && !typing) { e.preventDefault(); setHelp('shortcuts'); }
    };
    addEventListener('keydown', f);
    return () => removeEventListener('keydown', f);
  }, []);
  const mainRef = useRef<HTMLElement>(null);
  const blobs = useRef<Record<string, { url: string; type: string }>>({});
  useEffect(() => {
    const el = document.documentElement;
    el.style.colorScheme = dark ? 'dark' : 'light';
    el.style.backgroundColor = (dark ? darkTheme : theme).appBg;
    el.dir = dir;
    el.lang = 'en';
  }, [dark, dir]);
  useEffect(() => {
    const f = () => { setPage(parse()); setNotesOpen(false); };
    addEventListener('hashchange', f); // typed or pasted addresses
    addEventListener('popstate', f); // Back and Forward
    return () => { removeEventListener('hashchange', f); removeEventListener('popstate', f); };
  }, []);
  // a new page starts at the top, with focus on its content (screen readers hear the new page)
  useEffect(() => { mainRef.current?.scrollTo(0, 0); mainRef.current?.focus({ preventScroll: true }); }, [page]);

  const go = (p: string, id?: string) => {
    const target = ALIAS[p] || p;
    const hash = `#/${target}${id ? `/${encodeURIComponent(id)}` : ''}`;
    if (location.hash !== hash) history.pushState(null, '', hash);
    setPage({ page: target, id });
    setNotesOpen(false);
  };
  const notify = (title: string, intent: Intent = 'success', body?: string) =>
    dispatchToast(<Toast className={ss.toast}><ToastTitle>{title}</ToastTitle>{body && <ToastBody>{body}</ToastBody>}</Toast>, { intent, position: 'bottom-end' });
  const pushNote = (n: Omit<Note, 'id' | 'time'>) => { setNotes((all) => [{ id: `n-${Date.now()}`, time: 'Just now', ...n }, ...all]); setUnread((u) => u + 1); };

  // Simulated pipeline: each document walks its path, staggered so the batch reads naturally.
  const startRun = (items: UploadItem[], forced: string | null, source: string) => {
    const id = `R-${Math.max(...runs.map((r) => Number(r.id.slice(2)))) + 1}`;
    const docs = items.map((it, i): RunDoc => {
      const sampleId = it.sampleId || matchSample(it.name);
      const doc = sampleId ? DOCS[sampleId] : null;
      if (it.file) blobs.current[`${id}-${i}`] = { url: URL.createObjectURL(it.file), type: it.file.type };
      return {
        id: sampleId && !runs.some((r) => r.docs.some((d) => d.id === sampleId)) ? sampleId : `${id}-${i}`,
        sample: sampleId || null, name: it.name, pages: doc?.pages.length || 1, kind: doc?.kind || (/\.pdf$/i.test(it.name) ? 'vector' : 'scan'),
        profile: forced || 'freight', route: forced ? null : doc ? 0.97 : 0.52,
        status: 'queued', step: -1, score: null,
        // approved without a person only when auto-approve is on and nothing is below the check level
        final: !doc ? 'failed' : engines.cfg.autoAccept && !doc.fields.some((f) => f.conf < engines.cfg.accept) ? 'accepted' : 'review',
        reason: doc ? '' : "Couldn't read this file",
      };
    });
    setRuns((all) => [{ id, profile: forced || 'auto', source, started: 'Just now', by: ME.name, status: 'running', docs }, ...all]);
    log('Uploaded documents', `${id}, ${count(docs.length, 'file', 'files')}`);
    go('run', id);
    let tick = 0;
    const longest = Math.max(...docs.map((d, i) => PATHS[d.kind].length + i));
    const timer = setInterval(() => {
      tick += 1;
      setRuns((all) => all.map((run) => {
        if (run.id !== id) return run;
        const next = run.docs.map((d, i): RunDoc => {
          const len = PATHS[d.kind].length;
          const s = Math.min(tick - i, len);
          if (s < 0) return d;
          if (s === len) return { ...d, step: s, status: d.final ?? 'failed', score: d.sample ? meanConf(DOCS[d.sample]) : null };
          return { ...d, step: s, status: 'running' };
        });
        const done = next.every((d) => d.step === PATHS[d.kind].length);
        return { ...run, docs: next, status: (done ? (next.some((d) => d.status === 'review' || d.status === 'failed') ? 'review' : 'done') : 'running') as Run['status'] };
      }));
      if (tick > longest) {
        clearInterval(timer);
        const rev = docs.filter((d) => d.final === 'review').length;
        const failed = docs.filter((d) => d.final === 'failed').length;
        const body = [count(docs.length, 'document', 'documents'), rev && `${rev} need review`, failed && `${failed} failed`].filter(Boolean).join(', ');
        pushNote({ kind: 'run', title: `Batch ${id} finished`, body, page: ['run', id] });
        notify(`Batch ${id} finished`, failed ? 'warning' : 'success', body);
      }
    }, 420);
  };

  const allDocs = useMemo<Doc[]>(() => runs.flatMap((r) => r.docs.map((d) => ({ ...d, run: r.id, runProfile: r.profile }))), [runs]);
  const docRecord = (id: string): DocRecord | null => {
    const d = allDocs.find((x) => x.id === id);
    if (!d) return null;
    const sample = DOCS[d.sample || d.id] as DocRecord['sample'];
    return { ...d, sample, blob: blobs.current[d.id], values: { ...Object.fromEntries((sample?.fields || []).map((f) => [f.key, f.value])), ...(d.values || {}), ...(edits[d.id] || {}) } };
  };
  const reviewCount = allDocs.filter((d) => d.status === 'review').length;
  const removeSamples = () => {
    setRuns([]);
    setTables(Object.fromEntries(Object.entries(tables).map(([c, ts]) => [c, Object.fromEntries(Object.entries(ts).map(([n, t]) => [n, { ...t, rows: [] }]))])));
    setNotes([]); setUnread(0); setEdits({}); setSamples(false);
    log('Removed sample data', 'Workspace');
    notify('Sample data removed', 'success', 'Upload your own documents on Extract.');
  };
  const signOut = () => {
    try { localStorage.removeItem(SAVE_KEY); } catch { /* nothing was stored */ }
    setSignedIn(false);
  };
  const app: AppContextValue = {
    route: page, go, role, profiles, setProfiles, runs, setRuns, tables, setTables, workflows, setWorkflows, notify, pushNote, startRun,
    reviewCount, allDocs, docRecord, edits, setEdits, query, setQuery, dark, me: ME, engines, setEngines, engineDraft, setEngineDraft,
    flagBelow: engines.cfg.accept, audit, log, connections, setConnections, samples, removeSamples,
  };

  const admin = role === 'admin';
  const settings: SettingsItem[] = admin ? SETTINGS : [];
  const settingsKey = SETTINGS_OF[page.page] || page.page;
  const inSettings = settings.some(([k]) => k === settingsKey);
  const blocked = !admin && ADMIN_ONLY.has(page.page); // admin page while viewing as user
  const Page = blocked ? Home : PAGES[page.page] || Home;
  const railKey = blocked ? 'home' : inSettings ? 'settings' : page.page === 'run' ? 'extract' : page.page;
  const visibleNotes = notes.filter((n) => role === 'admin' || !n.admin);

  const railButton = (key: string, label: string, Icon: FluentIcon, onClick: () => void, extra = 0) => (
    <li key={key}>
      <Button appearance="subtle" className={mergeClasses('rail-btn', railKey === key && 'is-on', ss.countButton)} aria-current={railKey === key ? 'page' : undefined}
        aria-label={extra ? `${label}, ${extra} waiting` : undefined}
        icon={{ className: ss.railIcon, children: <IconWithCount icon={<Icon filled={railKey === key} fontSize={24} />} count={extra || 0} /> }} onClick={onClick}>
        <span className="rail-label">{label}</span>
      </Button>
    </li>
  );
  const railItems = RAIL.filter(([, , , adminOnly]) => admin || !adminOnly);
  const more: [string, string, FluentIcon][] = [
    ...railItems.filter(([k]) => !PHONE_TABS.has(k)).map(([k, label, Icon]): [string, string, FluentIcon] => [k, label, Icon]),
    ...(admin ? [['settings', 'Settings', SettingsIcon] as [string, string, FluentIcon]] : []),
  ];
  const moreOn = more.some(([k]) => k === railKey);

  if (!signedIn) {
    return (
      <FluentProvider theme={dark ? darkTheme : theme} dir={dir}>
        <main className="signed-out">
          <div className="signed-out-card">
            <span className="logo-chip signed-out-logo"><img src="/harsha-logo.png" alt="Harsha Engineers" className="logo" /></span>
            <Title>{"You're signed out"}</Title>
            <Body1 className="muted">Nothing from your session is left in this browser. Sign in again to get back to your documents.</Body1>
            <Button appearance="primary" className={ss.signIn} onClick={() => { history.replaceState(null, '', '#/home'); location.reload(); }}>Sign in</Button>
          </div>
        </main>
      </FluentProvider>
    );
  }

  return (
    <FluentProvider theme={dark ? darkTheme : theme} dir={dir}>
    <AppCtx.Provider value={app}>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="app">
        <header className="appbar" role="banner">
          <Button appearance="transparent" className={mergeClasses('brand', ss.headerButton)} onClick={() => go('home')} aria-label="Document Extraction home">
            <span className="logo-chip"><img src="/harsha-logo.png" alt="Harsha Engineers" className="logo" /></span>
            <span className="brand-name">Document Extraction</span>
          </Button>
          <form role="search" className="appbar-search" onSubmit={(e) => { e.preventDefault(); go('library'); }}>
            <SearchBox value={query} onChange={(_, d) => setQuery(d.value)} placeholder="Search documents, vendors, or invoice numbers" aria-label="Search documents" />
          </form>
          <div className="appbar-actions">
            <Tooltip content={unread ? `Notifications, ${unread} new` : 'Notifications'} relationship="label">
              <Button appearance="subtle" className={mergeClasses('bell', ss.headerButton, ss.countButton)} icon={<IconWithCount icon={<AlertRegular />} count={unread} />} onClick={() => { setNotesOpen(true); setUnread(0); }} />
            </Tooltip>
            <HelpMenu className={ss.headerButton} open={help} setOpen={setHelp} />
            <Menu checkedValues={{ role: [role], appearance: [appearance], lang: ['en'] }} onCheckedValueChange={(_, d) => {
              if (d.name === 'role') { setRole(d.checkedItems[0] as Role); notify(d.checkedItems[0] === 'admin' ? 'Back to your sysadmin view' : 'Previewing as a user', 'info'); }
              if (d.name === 'appearance') { setAppearance(d.checkedItems[0] as Appearance); store('appearance', d.checkedItems[0]); }
            }}>
              <MenuTrigger disableButtonEnhancement>
                <Tooltip content="Account" relationship="label">
                  <Button appearance="transparent" className={mergeClasses('me', ss.headerButton)} icon={<Avatar name={ME.name} size={28} color="neutral" />} />
                </Tooltip>
              </MenuTrigger>
              <MenuPopover>
                <div className="me-card"><Persona name={ME.name} secondaryText={ME.email} tertiaryText="Sysadmin" avatar={{ color: 'brand' }} /></div>
                <MenuDivider />
                <MenuList>
                  <MenuGroup>
                    <MenuGroupHeader>Preview as</MenuGroupHeader>
                    <MenuItemRadio name="role" value="admin">Sysadmin (you)</MenuItemRadio>
                    <MenuItemRadio name="role" value="user">User</MenuItemRadio>
                  </MenuGroup>
                  <MenuDivider />
                  <MenuGroup>
                    <MenuGroupHeader>Appearance</MenuGroupHeader>
                    <MenuItemRadio name="appearance" value="system">Use system setting</MenuItemRadio>
                    <MenuItemRadio name="appearance" value="light">Light</MenuItemRadio>
                    <MenuItemRadio name="appearance" value="dark">Dark</MenuItemRadio>
                  </MenuGroup>
                  <MenuDivider />
                  <MenuGroup>
                    <MenuGroupHeader>Language</MenuGroupHeader>
                    {LANGS.map(([k, v]) => <MenuItemRadio key={k} name="lang" value={k} lang={k} disabled={k !== 'en'} secondaryContent={k !== 'en' ? 'Coming soon' : undefined}>{v}</MenuItemRadio>)}
                  </MenuGroup>
                  <MenuDivider />
                  <MenuItem icon={<SignOutRegular />} onClick={signOut}>Sign out</MenuItem>
                </MenuList>
              </MenuPopover>
            </Menu>
          </div>
        </header>

        <div className="body">
          {/* Teams on desktop; on phones a bottom tab bar with the rest under More (Teams and Outlook mobile) */}
          <nav className={`rail${phone ? ' is-bar' : ''}`} aria-label="Navigation">
            <ul>
              {railItems.filter(([k]) => !phone || PHONE_TABS.has(k)).map(([k, label, Icon]) => railButton(k, label, Icon, () => go(k), k === 'review' ? reviewCount : 0))}
              {phone && more.length > 0 && (
                <li>
                  <Menu positioning="above-end">
                    <MenuTrigger disableButtonEnhancement>
                      <Button appearance="subtle" className={mergeClasses('rail-btn', moreOn && 'is-on')} icon={{ className: ss.railIcon, children: <MoreIcon filled={moreOn} fontSize={24} /> }}><span className="rail-label">More</span></Button>
                    </MenuTrigger>
                    <MenuPopover>
                      <MenuList>
                        {more.map(([k, label, Icon]) => <MenuItem key={k} icon={<Icon />} onClick={() => go(k === 'settings' ? settings[0][0] : k)}>{label}</MenuItem>)}
                      </MenuList>
                    </MenuPopover>
                  </Menu>
                </li>
              )}
            </ul>
            {admin && !phone && <ul>{railButton('settings', 'Settings', SettingsIcon, () => go(settings[0][0]))}</ul>}
          </nav>
          <main id="main" ref={mainRef} tabIndex={-1} className="main">
            {role !== 'admin' && (
              <MessageBar intent="info" className="view-as">
                <MessageBarBody>{"You're previewing the app as a user sees it."}</MessageBarBody>
                <MessageBarActions><Button onClick={() => { setRole('admin'); notify('Back to your sysadmin view', 'info'); }}>Switch back</Button></MessageBarActions>
              </MessageBar>
            )}
            <PageBoundary key={`${page.page}-${page.id || ''}`}>
            <Suspense fallback={<PageLoading />}>
            {inSettings ? (
              <div className="settings">
                <div className="settings-nav">
                  <Title as="p" className="settings-title" aria-hidden="true">Settings</Title>
                  <TabList vertical selectedValue={settingsKey} onTabSelect={(_, d) => go(String(d.value))} aria-label="Settings" className="settings-tabs">
                    {settings.map(([k, label, Icon]) => <Tab key={k} value={k} icon={<Icon />}>{label}</Tab>)}
                  </TabList>
                  {/* Fluent tablist guidance: in small layouts a dropdown replaces the tabs */}
                  <Dropdown className="settings-select" aria-label="Settings section" value={settings.find(([k]) => k === settingsKey)?.[1]}
                    selectedOptions={[settingsKey]} onOptionSelect={(_, d) => go(d.optionValue ?? settingsKey)}>
                    {settings.map(([k, label]) => <Option key={k} value={k}>{label}</Option>)}
                  </Dropdown>
                </div>
                <div className="settings-body"><Page key={`${page.page}-${page.id || ''}-${role}`} /></div>
              </div>
            ) : <Page key={`${page.page}-${page.id || ''}-${role}`} />}
            </Suspense>
            </PageBoundary>
          </main>
        </div>
      </div>

      <OverlayDrawer className="acrylic" position="end" open={notesOpen} onOpenChange={(_, d) => { setNotesOpen(d.open); if (!d.open) setSeen(new Set(notes.map((n) => n.id))); }} modalType="non-modal" size="small">
        <DrawerHeader>
          <DrawerHeaderTitle action={<Button appearance="subtle" aria-label="Close" icon={<DismissRegular />} onClick={() => setNotesOpen(false)} />}>
            Notifications
          </DrawerHeaderTitle>
        </DrawerHeader>
        <DrawerBody>
          {visibleNotes.length === 0 ? (
            <EmptyState picto="bell" title="You're all caught up" body="New activity on your documents shows up here." compact />
          ) : (
            <>
              <List navigationMode="items" className="notes" aria-label="Notifications">
                {visibleNotes.map((n) => (
                  <ListItem key={n.id} value={n.id} className={`note${seen.has(n.id) ? '' : ' is-new'}`} onAction={() => go(n.page[0], n.page[1])} aria-label={`${seen.has(n.id) ? '' : 'New. '}${n.title}`}>
                    <Glyph icon={NOTE_ICON[n.kind][0]} tone={NOTE_ICON[n.kind][1]} />
                    <span className="note-text">
                      <span className="note-top"><Body1 className="note-title">{n.title}</Body1><Caption1 className="muted nowrap">{n.time}</Caption1></span>
                      {n.body && <Caption1 block className="muted">{n.body}</Caption1>}
                    </span>
                  </ListItem>
                ))}
              </List>
              <Button appearance="subtle" onClick={() => setNotes((all) => all.filter((n) => !visibleNotes.includes(n)))}>Clear all</Button>
            </>
          )}
        </DrawerBody>
      </OverlayDrawer>
      <Toaster toasterId={toasterId} />
    </AppCtx.Provider>
    </FluentProvider>
  );
}
