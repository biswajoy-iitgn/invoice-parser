import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Avatar, Button, Menu, MenuTrigger, MenuPopover, MenuList, MenuItemRadio, MenuGroup, MenuGroupHeader, MenuDivider,
  Popover, PopoverTrigger, PopoverSurface, Toaster, Toast, ToastTitle, ToastBody, useToastController, useId, Tooltip, Text,
  List, ListItem, SearchBox, Dropdown, Option, OverlayDrawer, DrawerHeader, DrawerHeaderTitle, DrawerBody, Link, Body1, Caption1, Persona, TabList, Tab,
} from '@fluentui/react-components';
import {
  bundleIcon, HomeFilled, HomeRegular, DocumentSearchFilled, DocumentSearchRegular, LibraryFilled, LibraryRegular,
  DataTrendingFilled, DataTrendingRegular, SettingsFilled, SettingsRegular, DatabaseFilled, FlowFilled,
  AppsListDetailRegular, FlowRegular, ServerRegular, DatabaseRegular, PeopleRegular, ShieldTaskRegular, ArrowSyncCircleRegular,
  AlertRegular, QuestionCircleRegular, DismissRegular,
} from '@fluentui/react-icons';
import { AppCtx, EmptyState, Picto, IconWithCount, Title } from './ui';
import { PROFILES, RUNS, TABLES, WORKFLOWS, PATHS, NOTIFICATIONS, DOCS, docStatus, fieldValue, meanConf } from './data';
import Home from './pages/Home';
import { RunDetail } from './pages/Runs';
import Review from './pages/Review';
import Library from './pages/Library';
import Insights from './pages/Insights';
import { Profiles, ProfileEditor } from './pages/Profiles';
import Data from './pages/Data';
import Workflows from './pages/Workflows';
import Engines from './pages/Engines';
import { Users, Audit, Updates } from './pages/Admin';

// Teams-style app rail: the places people work, role-based; system settings at the bottom.
// [route, label, icon, admin only]
const RAIL = [
  ['home', 'Home', bundleIcon(HomeFilled, HomeRegular), false],
  ['review', 'Review', bundleIcon(DocumentSearchFilled, DocumentSearchRegular), false],
  ['library', 'Documents', bundleIcon(LibraryFilled, LibraryRegular), false],
  ['data', 'Databases', bundleIcon(DatabaseFilled, DatabaseRegular), false],
  ['workflows', 'Automations', bundleIcon(FlowFilled, FlowRegular), true],
  ['insights', 'Insights', bundleIcon(DataTrendingFilled, DataTrendingRegular), false],
];
const SettingsIcon = bundleIcon(SettingsFilled, SettingsRegular);
const SETTINGS = [
  ['profiles', 'Profiles', AppsListDetailRegular],
  ['engines', 'AI and engines', ServerRegular],
  ['users', 'People', PeopleRegular],
  ['audit', 'Audit log', ShieldTaskRegular],
  ['updates', 'Updates', ArrowSyncCircleRegular],
];
const ADMIN_ONLY = new Set(['workflows', 'profile', ...SETTINGS.map(([k]) => k)]);
const PAGES = {
  home: Home, run: RunDetail, review: Review, library: Library, insights: Insights, data: Data,
  profiles: Profiles, profile: ProfileEditor, workflows: Workflows, engines: Engines, users: Users, audit: Audit, updates: Updates,
};
const ALIAS = { runs: 'library', settings: 'profiles', automations: 'workflows', databases: 'data' };
const SETTINGS_OF = { profile: 'profiles' };
const NOTE_PICTO = { approval: 'check_mark_button', run: 'inbox_tray', insight: 'light_bulb', engine: 'electric_plug' };
const LANGS = [['en', 'English'], ['gu', 'ગુજરાતી'], ['hi', 'हिन्दी']];

// Demo reader: a file whose name matches a bundled sample opens that sample's real pages and values.
function matchSample(name) {
  const n = name.toLowerCase().replace(/[_\s-]+/g, ' ');
  const hit = Object.values(DOCS).find((d) => {
    const base = d.name.toLowerCase().replace(/\.\w+$/, '').replace(/[_\s-]+/g, ' ').replace(' phone', '');
    return n.includes(base) && d.kind === (/(phone|jpg|jpeg|png|heic)/.test(n) ? 'scan' : 'vector');
  });
  return hit?.id;
}

// Hash routes (#/review/doc-dachser) so Back works and pages can be linked.
const parse = () => {
  const [p, id] = location.hash.replace(/^#\/?/, '').split('/');
  return { page: ALIAS[p] || p || 'home', id: id ? decodeURIComponent(id) : undefined };
};

export default function App() {
  const [page, setPage] = useState(parse);
  const [role, setRole] = useState('admin');
  const [lang, setLang] = useState('en');
  const [profiles, setProfiles] = useState(PROFILES);
  const [runs, setRuns] = useState(RUNS);
  const [tables, setTables] = useState(TABLES);
  const [workflows, setWorkflows] = useState(WORKFLOWS);
  const [edits, setEdits] = useState({}); // docId -> { fieldKey: value }
  const [notes, setNotes] = useState(NOTIFICATIONS);
  const [unread, setUnread] = useState(3);
  const [notesOpen, setNotesOpen] = useState(false);
  const [seen, setSeen] = useState(new Set()); // notifications read in an earlier visit to the panel
  const [query, setQuery] = useState('');
  const toasterId = useId('toaster');
  const { dispatchToast } = useToastController(toasterId);
  const mainRef = useRef(null);
  const blobs = useRef({});
  useEffect(() => {
    const f = () => { setPage(parse()); setNotesOpen(false); mainRef.current?.scrollTo(0, 0); };
    addEventListener('hashchange', f);
    return () => removeEventListener('hashchange', f);
  }, []);

  const go = (p, id) => {
    const target = ALIAS[p] || p;
    const hash = `#/${target}${id ? `/${encodeURIComponent(id)}` : ''}`;
    if (location.hash !== hash) location.hash = hash; else setPage({ page: target, id });
    setNotesOpen(false);
    mainRef.current?.scrollTo(0, 0);
    mainRef.current?.focus();
  };
  const notify = (title, intent = 'success', body) =>
    dispatchToast(<Toast><ToastTitle>{title}</ToastTitle>{body && <ToastBody>{body}</ToastBody>}</Toast>, { intent, position: 'bottom-end' });
  const pushNote = (n) => { setNotes((all) => [{ id: `n-${Date.now()}`, time: 'Just now', ...n }, ...all]); setUnread((u) => u + 1); };

  // Simulated pipeline: each document walks its path, staggered so the batch reads naturally.
  const startRun = (items, forced, source) => {
    const id = `R-${1043 + runs.length - RUNS.length}`;
    const docs = items.map((it, i) => {
      const sampleId = it.sampleId || matchSample(it.name);
      const doc = sampleId ? DOCS[sampleId] : null;
      if (it.file) blobs.current[`${id}-${i}`] = { url: URL.createObjectURL(it.file), type: it.file.type };
      return {
        id: sampleId && !runs.some((r) => r.docs.some((d) => d.id === sampleId)) ? sampleId : `${id}-${i}`,
        sample: sampleId || null, name: it.name, pages: doc?.pages.length || 1, kind: doc?.kind || (/\.pdf$/i.test(it.name) ? 'vector' : 'scan'),
        profile: forced || 'freight', route: forced ? null : doc ? 0.97 : 0.52,
        status: 'queued', step: -1, score: null,
        final: doc ? docStatus(doc) : 'failed',
        reason: doc ? '' : 'The demo reads only the bundled sample invoices',
      };
    });
    setRuns((all) => [{ id, profile: forced || 'auto', source, started: 'Just now', by: 'Biswajoy Bhattacharjee', status: 'running', docs }, ...all]);
    go('run', id);
    let tick = 0;
    const longest = Math.max(...docs.map((d, i) => PATHS[d.kind].length + i));
    const timer = setInterval(() => {
      tick += 1;
      setRuns((all) => all.map((run) => {
        if (run.id !== id) return run;
        const next = run.docs.map((d, i) => {
          const len = PATHS[d.kind].length;
          const s = Math.min(tick - i, len);
          if (s < 0) return d;
          if (s === len) return { ...d, step: s, status: d.final, score: d.sample ? meanConf(DOCS[d.sample]) : null };
          return { ...d, step: s, status: 'running' };
        });
        const done = next.every((d) => d.step === PATHS[d.kind].length);
        return { ...run, docs: next, status: done ? (next.some((d) => d.status === 'review' || d.status === 'failed') ? 'review' : 'done') : 'running' };
      }));
      if (tick > longest) {
        clearInterval(timer);
        const rev = docs.filter((d) => d.final === 'review').length;
        const failed = docs.filter((d) => d.final === 'failed').length;
        const body = [`${docs.length} documents`, rev && `${rev} need review`, failed && `${failed} failed`].filter(Boolean).join(', ');
        pushNote({ kind: 'run', title: `Batch ${id} finished`, body, page: ['run', id] });
        notify(`Batch ${id} finished`, failed ? 'warning' : 'success', body);
      }
    }, 420);
  };

  const allDocs = useMemo(() => runs.flatMap((r) => r.docs.map((d) => ({ ...d, run: r.id, runProfile: r.profile }))), [runs]);
  const docRecord = (id) => {
    const d = allDocs.find((x) => x.id === id);
    if (!d) return null;
    const sample = DOCS[d.sample || d.id];
    return { ...d, sample, blob: blobs.current[d.id], values: { ...Object.fromEntries((sample?.fields || []).map((f) => [f.key, f.value])), ...(edits[d.id] || {}) } };
  };
  const reviewCount = allDocs.filter((d) => d.status === 'review').length;
  const app = {
    route: page, go, role, profiles, setProfiles, runs, setRuns, tables, setTables, workflows, setWorkflows, notify, pushNote, startRun,
    reviewCount, allDocs, docRecord, edits, setEdits, query, setQuery, fieldValue,
  };

  const admin = role === 'admin';
  const settings = admin ? SETTINGS : [];
  const settingsKey = SETTINGS_OF[page.page] || page.page;
  const inSettings = settings.some(([k]) => k === settingsKey);
  const blocked = !admin && ADMIN_ONLY.has(page.page); // admin page while viewing as user
  const Page = blocked ? Home : PAGES[page.page] || Home;
  const railKey = blocked ? 'home' : inSettings ? 'settings' : page.page === 'run' ? 'library' : page.page;
  const visibleNotes = notes.filter((n) => role === 'admin' || !n.admin);

  const railButton = (key, label, Icon, onClick, extra) => (
    <li key={key}>
      <Button appearance="subtle" className={`rail-btn${railKey === key ? ' is-on' : ''}`} aria-current={railKey === key ? 'page' : undefined}
        icon={<IconWithCount icon={<Icon filled={railKey === key} fontSize={24} />} count={extra || 0} label={`${extra} waiting`} />} onClick={onClick}>
        <span className="rail-label">{label}</span>
      </Button>
    </li>
  );

  return (
    <AppCtx.Provider value={app}>
      <a className="skip-link" href="#main">Skip to content</a>
      <div className="app">
        <header className="appbar" role="banner">
          <Button appearance="transparent" className="brand" onClick={() => go('home')} aria-label="Document Extraction home">
            <span className="logo-chip"><img src="/harsha-logo.png" alt="Harsha Engineers" className="logo" /></span>
            <span className="brand-name">Document Extraction</span>
          </Button>
          <form role="search" className="appbar-search" onSubmit={(e) => { e.preventDefault(); go('library'); }}>
            <SearchBox value={query} onChange={(_, d) => setQuery(d.value)} placeholder="Search documents, vendors, or invoice numbers" aria-label="Search documents" />
          </form>
          <div className="appbar-actions">
            <Tooltip content={unread ? `Notifications, ${unread} new` : 'Notifications'} relationship="label">
              <Button appearance="subtle" className="bell" icon={<IconWithCount icon={<AlertRegular />} count={unread} label={`${unread} new`} />} onClick={() => { setNotesOpen(true); setUnread(0); }} />
            </Tooltip>
            <Popover positioning="below-end" withArrow>
              <PopoverTrigger disableButtonEnhancement>
                <Tooltip content="Help" relationship="label"><Button appearance="subtle" icon={<QuestionCircleRegular />} /></Tooltip>
              </PopoverTrigger>
              <PopoverSurface className="help" aria-label="Help">
                <Text as="h2" weight="semibold" size={400}>Need help?</Text>
                <Body1>Drop files on Home. The AI reads them, and anything it's unsure about waits for you in Review.</Body1>
                <Link onClick={() => go('home')}>Go to Home</Link>
                <Link href="mailto:biswajoy.b@iitgn.ac.in?subject=Document%20Extraction%20help">Email your administrator</Link>
              </PopoverSurface>
            </Popover>
            <Menu checkedValues={{ role: [role], lang: [lang] }} onCheckedValueChange={(_, d) => {
              if (d.name === 'role') { setRole(d.checkedItems[0]); notify(`Viewing as ${d.checkedItems[0] === 'admin' ? 'sysadmin' : 'user'}`, 'info'); }
              if (d.name === 'lang') { setLang(d.checkedItems[0]); if (d.checkedItems[0] !== 'en') notify('Only English text is available in this demo', 'info'); }
            }}>
              <MenuTrigger disableButtonEnhancement>
                <Tooltip content="Account" relationship="label">
                  <Button appearance="transparent" className="me" icon={<Avatar name="Biswajoy Bhattacharjee" size={28} color="neutral" />} />
                </Tooltip>
              </MenuTrigger>
              <MenuPopover>
                <div className="me-card"><Persona name="Biswajoy Bhattacharjee" secondaryText={role === 'admin' ? 'Sysadmin' : 'User'} avatar={{ color: 'brand' }} /></div>
                <MenuDivider />
                <MenuList>
                  <MenuGroup>
                    <MenuGroupHeader>Demo role</MenuGroupHeader>
                    <MenuItemRadio name="role" value="user">User</MenuItemRadio>
                    <MenuItemRadio name="role" value="admin">Sysadmin</MenuItemRadio>
                  </MenuGroup>
                  <MenuDivider />
                  <MenuGroup>
                    <MenuGroupHeader>Language</MenuGroupHeader>
                    {LANGS.map(([k, v]) => <MenuItemRadio key={k} name="lang" value={k} lang={k}>{v}</MenuItemRadio>)}
                  </MenuGroup>
                </MenuList>
              </MenuPopover>
            </Menu>
          </div>
        </header>

        <div className="body">
          <nav className="rail" aria-label="Navigation">
            <ul>
              {RAIL.filter(([, , , adminOnly]) => admin || !adminOnly).map(([k, label, Icon]) => railButton(k, label, Icon, () => go(k),
                k === 'review' ? reviewCount : 0))}
            </ul>
            {admin && <ul>{railButton('settings', 'Settings', SettingsIcon, () => go(settings[0][0]))}</ul>}
          </nav>
          <main id="main" ref={mainRef} tabIndex={-1} className="main">
            {inSettings ? (
              <div className="settings">
                <div className="settings-nav">
                  <Title as="p" className="settings-title" aria-hidden="true">Settings</Title>
                  <TabList vertical selectedValue={settingsKey} onTabSelect={(_, d) => go(d.value)} aria-label="Settings" className="settings-tabs">
                    {settings.map(([k, label, Icon]) => <Tab key={k} value={k} icon={<Icon />}>{label}</Tab>)}
                  </TabList>
                  {/* Fluent tablist guidance: in small layouts a dropdown replaces the tabs */}
                  <Dropdown className="settings-select" aria-label="Settings section" value={settings.find(([k]) => k === settingsKey)?.[1]}
                    selectedOptions={[settingsKey]} onOptionSelect={(_, d) => go(d.optionValue)}>
                    {settings.map(([k, label]) => <Option key={k} value={k}>{label}</Option>)}
                  </Dropdown>
                </div>
                <div className="settings-body"><Page key={`${page.page}-${page.id || ''}-${role}`} /></div>
              </div>
            ) : <Page key={`${page.page}-${page.id || ''}-${role}`} />}
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
                  <ListItem key={n.id} value={n.id} className={`note${seen.has(n.id) ? '' : ' is-new'}`} onAction={() => go(...n.page)} aria-label={`${seen.has(n.id) ? '' : 'New. '}${n.title}`}>
                    <span className="note-picto"><Picto name={NOTE_PICTO[n.kind]} size={28} /></span>
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
  );
}
