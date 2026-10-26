# UI Decisions (grill, 24 Oct 2026)

Stack: React 18 + Fluent UI v9 (`@fluentui/react-components`, `react-nav`, `react-charts`), Harsha theme from HDS.PALETTE. Mock data only.

| Area | Decision |
|---|---|
| Name | Document Extraction (generic); profiles: Freight Invoice, Purchase Order, Delivery Note |
| Roles | User, Sysadmin. Demo-only role toggle in account menu |
| Profiles | Sysadmin only creates or edits. Users pick one on upload |
| Router | Upload defaults to Auto-Detect, user can override. Sysadmin configures classifier + threshold, keyword/vendor rules, source mapping, unknown → Review |
| Pipeline | Router → Vector Text (vector PDF) or Upscaler (scan) → Layout → OCR (scans only) → LLM → Decision → Evaluator |
| Progress | Per-document engine stage; notification when run ends |
| Review | Single approver; Approve locked until every flagged field is edited or confirmed. Page thumbnails, field select jumps to page |
| Failures | Show reason; Retry, Retry at Higher Quality |
| Engines | Global connections per engine, ordered fallback chain. Local or API. Auth: API key, bearer, OAuth client credentials, cloud IAM role. Monthly budget per API connection |
| Privacy | PII redaction before API calls; per-profile On-Premises Only lock |
| Data | External DBs. Users: read-only, profile-linked tables only. Sysadmin: edit, add, delete, schema, connections |
| Library | Built-in store of every document, field, box, score, edit; searchable |
| Export | CSV, Excel, Database, Run Workflow |
| Workflows | Webhook, Email/Teams, Approval Routing (sysadmin manages) |
| Insights | Volume, accuracy, business (spend, charge mix, anomalies, duplicates), engine health and cost (sysadmin). Nightly + on-demand refresh |
| Admin | Users & Roles, Audit Trail, Engine Updates |
| Notifications | Run complete or failed, approvals for me, engine offline or budget (sysadmin), new insights |
| Theme | Light (Precision) only. Bokashi: Harsha sky in the suite header (deep to brand, white text 5.6:1+), Growth on the batch-finished band |
| Language | English UI; language menu stubbed (English, Gujarati, Hindi) |
| Type | Harsha font: Eurostile Extended (700, repaired TTF from HarshaLMS_V2) for titles only, section headings included (kept by choice on 25 Oct over Fluent's Noto Sans Semibold subtitle); Noto Sans for body, labels, buttons, Gujarati, Devanagari. Fluent type ramp sizes |

## Revision, 24 Oct (after review: too busy, sidebar, not Microsoft-like)

| Area | Decision |
|---|---|
| Inspiration | OneDrive / SharePoint library (command bar, filter pills, list), Outlook (list + reading pane), AI Builder validation station (low-confidence fields first), M365 suite header |
| Navigation | Teams-style app rail: Home, Review, Documents, Databases, Automations (sysadmin), Insights; Settings at the bottom (sysadmin) holds Profiles, AI and engines, People, Audit log, Updates. Below 1024 px Settings sections switch to a dropdown |
| Home | Welcome band (greeting, then one row: Upload files / Upload a folder / Try the samples as solid tiles and Document type at its end; no count line, the rail badge and "Waiting for you" already carry it), up to 4 "Waiting for you" file cards, "Your workspace" strip (Databases, Automations, AI and engines, Insights by role), Recent list. Drop files anywhere on the page |
| Review | Landing: file cards for the queue. Document: Outlook-style queue pane (from 1280 px; up/down arrows below that), page with marked locations and an acrylic page/zoom toolbar, values pane with one check card per flagged value (crop of the printed spot with a line of context, value centred, edges faded, up to 2.2x on 160 dpi pages), other values and checks behind accordions. Approve locked until flagged values are edited or confirmed; the reason ("Check 2 values to approve") is written beside it |
| Documents | Two tabs. Files: grid with selection, export of the selection, batch dropdown, status pills; batches live here (batch page via link). Extracted data (`#/library/data`): one row per document, a column per profile field grouped as in the profile, document name pinned while scrolling sideways, values still to check highlighted, export of the table |
| Copy | Sentence case everywhere (Fluent web), plain names for engines on user screens; technical names only in AI and engines |
| AI | Disclaimer "AI-generated content may be incorrect. Check for accuracy." and thumbs feedback with issue categories on every document |
| Materials | Mica-style base: `#F0F3F7` with one faint sky wash under the header, behind a translucent rail and Settings nav (blur 48 px). The Home hero is the only colourful surface: a white card whose top carries a slow Harsha sky-growth aurora (three wide one-hue blooms, sky on the left into growth on the right, drifting over 22 to 27 s, faded out before the tiles, still under reduced motion). Acrylic (blur 24 to 30 px) on the command bar, page toolbar, menus, popovers, listboxes, toasts, notifications. Content cards stay solid; smoke behind dialogs. `prefers-reduced-transparency` and no `backdrop-filter` support fall back to solid fills |
| Elevation | Cards 4, hero/bands 8, upload/flyouts 16, dialogs 64; rail and header separated by strokes |
| Wait UX | Labelled progress for batches ("Reading documents …", n of m), skeleton while page images load, spinner + label for refresh/test, nothing under 1 s, role=status announcements |
| Empty states | Fluent Emoji 3D pictogram (MIT, pinned commit), one line, one action |
| Data | Real pages and values from `sample invoices/` and phone photos (`scripts/build_samples.py`); boxes located with PyMuPDF; eval gold not used |

## Revision, 25 Oct (after review: Microsoft quality bar, materials, missing features)

| Area | Decision |
|---|---|
| Thumbnails | File cards show the page top in a grey tray with `object-fit: cover`; Fluent CardPreview stretched it before |
| Gradients | Harsha bokashi, one hue each: sky in the suite header, the sky-growth aurora in the Home hero only, growth for "All checked" and "Batch finished", seal for approved. Steel unused |
| Pictures | Fluent Emoji 3D (MIT, pinned commit) on page headers, workspace cards, profile and automation cards, KPI cards, empty states. Saturated purple ones dropped (file cabinet, check box): index dividers for Databases, green check for approval |
| Rail | 80 px so 12 px labels (Fluent nav size) never clip; selection as in Teams: filled brand icon, semibold label, left bar, no pill; counters on the icon's corner |
| Controls on white | Dropdowns and search boxes on white or near-white surfaces use the outline appearance, so their edge meets 3:1 (filled-lighter is only for grey surfaces) |
| Phones | tables become cards with each label above its value below 480 px |
| Human touches | Time-of-day greeting with the user's name, unread notifications, an "All checked" moment before Approve, a "Skip for now" link to the next document |
| Quality gate | `npm run qa`: palette membership, one-hue gradients, 33 contrast pairs (worst case on translucent fills), sentence-case copy lint, Fluent-only controls, assets, dependencies. `scripts/qa-browser.js` in the running app: axe WCAG 2.2 AA, one h1, heading order, alt text, control names, 24 px targets, no horizontal scroll at 1366/683/341 px, console errors, dead clicks |

## Revision, 25 Oct (design review: clarity and hierarchy)

| Area | Decision |
|---|---|
| Confidence | "81% sure" is a neutral badge and flagged spots are outlined in Harsha Blue: a value to check is not an error. Red stays for failures and failed checks |
| Pictures | Workspace strip and matching page headers use saturated 3D pictures: index dividers (Databases), blue arrows (Automations), puzzle piece (AI and engines), bar chart (Insights); Updates moved to the package |
| Charts | Full axis labels (vendors shortened, spend in INR lakh), whole-number ticks for counts, round ticks for volume, % on rates, no one-series legends, no pictures on chart cards |
| Wording | Batch "Confidence", not "Accuracy" (it is the AI's own score); profiles "include" fields; profile cards say who uses them instead of repeating the name |
| Engines | Each engine page has its own address (`#/engines/ocr`), so Back and shared links work; a primary connection that is down while its fallback runs shows "On fallback", not a red "Offline" |
| Dialogs | Primary button first, then Cancel (Fluent 2: primary on the left) |
| Avatars | Harsha Blue for everyone, not Fluent's colourful set |

## Revision, 25 Oct (Microsoft review blockers, then user review)

| Area | Decision |
|---|---|
| Navigation | Rail: Home, Extract, Review, Documents, Databases, Automations (sysadmin), Insights; Settings at the bottom. Rail 88 px; the selection bar is a short bar in a 6 px gutter level with the icon (Teams), so it never sits beside a label. Phones: bottom tab bar (Home, Extract, Review, Documents, More) |
| Extract | Its own page: one drop target (files or a folder), document type, what is reading now, and every batch. Batches open from here; Home only links to it |
| Home | Overview only: greeting with Extract documents and Review next, Waiting for you, Recent. The workspace strip went: every area is on the rail |
| Extracted data | A workbook like the live Excel export: a sheet per document type plus Charges (one row per charge: Sl. no., Category, Invoice no., Vendor, Date, Amount). Gridlines, frozen header and first columns, arrow keys move cell to cell, totals row, sheet tabs at the bottom |
| Review | Page and zoom controls docked above the page, never over the print; page arrows only for multi-page documents. A value's highlight clears once it is checked. Queue is one Tab stop, arrow keys move through it |
| Pictures | Fluent Emoji 3D only in empty states. Cards, notifications and hero tiles use Fluent icons on a soft brand tile; page headers and figures have no pictures. A glossy-glyph icon set was tried and dropped |
| Appearance | Use system setting, Light or Dark (HDS dark column). High Contrast keeps value outlines, selection and counts in system colours. Charts are 3:1 on their card, with gaps between stacked parts, and every chart can show its numbers as a table |
| Data | One seeded timeline (15 to 24 Oct): Insights, databases, audit and notifications are all computed from the same 108 documents |
| Header | Hover darkens the header and icons stay white (Fluent tints subtle-button icons blue, which vanished on the blue bar). Count badges are not clipped by their buttons |
| Quality gate | qa-browser.js also checks what people see: content clipped by a parent, placeholders and values cut off, truncated labels, overlapping text, and selection bars within 4 px of a label. `npm run qa:hover` hovers every header, rail and command-bar button in light and dark and measures icon and label contrast against the real backdrop. Each was proved by re-introducing the defect it targets |

## Revision, 25 Oct (senior frontend review: engineering bar)

| Area | Decision |
|---|---|
| Code | TypeScript strict across the app (`src/types.ts` is the domain model); ESLint with typescript-eslint, React, React Hooks (compiler rules) and jsx-a11y strict. Pages load lazily behind an error boundary |
| Styling | Fluent components are styled only through `makeStyles` on their className or slots (`src/styles.ts`, the table wrappers in `ui.tsx`, the sheet in `Library.tsx`). `index.css` lays out the app's own elements: no rule targets a Fluent class, no `!important` |
| Theme | App colours are tokens in the Fluent theme, so light and dark switch with the provider. `public/boot.js` paints the saved or system background before the app loads, so dark mode never flashes white |
| State | Page, tab, sheet, batch, status and search live in the address (`#/library/data?sheet=charges`); Back and Forward work and focus moves to the page. The workspace persists across reloads |
| Tables | Tables keep explicit roles when they reflow to cards on narrow screens. Extracted data is an ARIA grid with one Tab stop. On narrow screens the Files list is a grid of rows whose checkbox, name and status are cells |
| Formatting | Numbers, money (Indian grouping), percentages and plural forms go through Intl for `en-IN` (`src/format.ts`). Strings stay inline until a second language ships; the Language menu says Coming soon |
| Samples | Sample pages are redacted with PyMuPDF (emails, people, phone and bank details, signatures), never just covered; phone photos are generated from the redacted pages |
| Security | The built app carries a Content Security Policy: scripts from the app only, styles inline for Griffel, fonts from Google Fonts, blob previews for uploaded PDFs |
| Checks | `npm run check` runs lint, types, static QA, unit tests (Vitest, Testing Library), the build and a gzip budget (entry 260 kB, lazy chunk 95 kB, CSS 12 kB). `npm run e2e` runs every page in Chrome at 1366, 683 and 341 px, light and dark, through axe and the layout checks. CI runs both |

## Revision, 26 Oct (PM review: scale, any document type, databases as sheets, trust)

| Area | Decision |
|---|---|
| Paging | Long lists page (25, 50 or 100 rows; Microsoft list pattern with first, previous, next, last): Documents, every sheet, database tables, Review, batches, audit log. Sl. no. and totals count across pages; the grid tells screen readers the full row count |
| Profiles | Any kind of document. A profile names its kind freely and starts from a template (invoice, purchase order, delivery note, contract, letter, datasheet) or from no fields. Fields can be added, edited and removed; types are text, long text, number, amount, date, yes or no, and table (with columns); "How to find it" tells the AI where the value is |
| Databases | One workbook per database: a sheet per table with tabs at the foot, as in Excel. Edit in place (double-click, Enter, F2 or type; Enter and Tab save and move), delete and keep rows, then Save or Discard together. Schema draws the tables and every foreign key (crow's foot at the many end), with the same relationships as a table under it |
| Exports | Excel is a real .xlsx with the Documents sheets: one per profile plus Charges, amounts and dates typed, bold frozen header. CSV is UTF-8 with a byte-order mark, and text that would start a formula is made literal |
| Engines | Settings are a draft until Save, kept across engine pages, and saved with the workspace. One threshold decides what a person checks (85 %); approving without a person is off by default. Profiles and Privacy show where each step runs and whether data leaves the network, from the actual engine connections |
| Trust | Approval records who approved, when, and which values were confirmed or corrected; an approved document shows that record and no longer highlights the page. Every change writes a SHA-256 hash-chained audit entry, and Verify recomputes the chain. Retry reads the file again. "Approved without corrections" counts corrections made in Review |
| Automations | Apply to all profiles or a list, so switching one profile off keeps the others. The webhook body is saved. Running one asks first and names the destination; only sysadmins see them in Export |
| Shell | Help: Get started, Keyboard shortcuts (Shift+?), Give feedback, About with versions; Alt+Q goes to search. Sign-out clears this browser's copy of the workspace. Sample data can be removed from Home. Fonts are served by the app, so no request goes to a font CDN |
| Wording | One word per state: Approved (not Accepted), Needs review (not To review) |
