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
| Type | Harsha font: Eurostile Extended (700, repaired TTF from HarshaLMS_V2) for titles only; Noto Sans for body, labels, buttons, Gujarati, Devanagari. Fluent type ramp sizes |

## Revision, 24 Oct (after review: too busy, sidebar, not Microsoft-like)

| Area | Decision |
|---|---|
| Inspiration | OneDrive / SharePoint library (command bar, filter pills, list), Outlook (list + reading pane), AI Builder validation station (low-confidence fields first), M365 suite header |
| Navigation | Teams-style app rail: Home, Review, Documents, Databases, Automations (sysadmin), Insights; Settings at the bottom (sysadmin) holds Profiles, AI and engines, People, Audit log, Updates. Below 1024 px Settings sections switch to a dropdown |
| Home | Welcome band (greeting, queue count, Upload files / Upload a folder / Try the samples, Read files as), up to 4 "Waiting for you" file cards, "Your workspace" strip (Databases, Automations, AI and engines, Insights by role), Recent list. Drop files anywhere on the page |
| Review | Landing: file cards for the queue. Document: page with marked locations and an acrylic page/zoom toolbar, values pane with one check card per flagged value (crop of the printed spot, value centred so its label shows; capped at 1.6x so 110 dpi pages stay sharp), other values and checks behind accordions. Approve locked, with a tooltip, until flagged values are edited or confirmed |
| Documents | Library grid with selection, export of the selection, batch dropdown, status pills; batches live here (batch page via link) |
| Copy | Sentence case everywhere (Fluent web), plain names for engines on user screens; technical names only in AI and engines |
| AI | Disclaimer "AI-generated content may be incorrect. Check for accuracy." and thumbs feedback with issue categories on every document |
| Materials | Mica-style base: soft sky and growth blooms on `#F0F3F7` behind a translucent rail, Settings nav and welcome band (blur 48 px). Acrylic (blur 24 to 30 px) on the command bar, page toolbar, menus, popovers, listboxes, toasts, notifications. Content cards stay solid; smoke behind dialogs. `prefers-reduced-transparency` and no `backdrop-filter` support fall back to solid fills |
| Elevation | Cards 4, hero/bands 8, upload/flyouts 16, dialogs 64; rail and header separated by strokes |
| Wait UX | Labelled progress for batches ("Reading documents …", n of m), skeleton while page images load, spinner + label for refresh/test, nothing under 1 s, role=status announcements |
| Empty states | Fluent Emoji 3D pictogram (MIT, pinned commit), one line, one action |
| Data | Real pages and values from `sample invoices/` and phone photos (`scripts/build_samples.py`); boxes located with PyMuPDF; eval gold not used |

## Revision, 25 Oct (after review: Microsoft quality bar, materials, missing features)

| Area | Decision |
|---|---|
| Thumbnails | File cards show the page top in a grey tray with `object-fit: cover`; Fluent CardPreview stretched it before |
| Gradients | Harsha bokashi, one hue each: sky in the suite header and welcome bloom, growth for "All checked" and "Batch finished", seal for approved. Steel unused |
| Pictures | Fluent Emoji 3D (MIT, pinned commit) on page headers, workspace cards, profile and automation cards, KPI cards, empty states. Saturated purple ones dropped (file cabinet, check box): index dividers for Databases, green check for approval |
| Phones | Rail keeps 72 px so labels never clip; tables become cards with each label above its value below 480 px |
| Human touches | Time-of-day greeting with the user's name, unread notifications, an "All checked" moment before Approve, a "Skip for now" link to the next document |
| Quality gate | `npm run qa`: palette membership, one-hue gradients, 33 contrast pairs (worst case on translucent fills), sentence-case copy lint, Fluent-only controls, assets, dependencies. `scripts/qa-browser.js` in the running app: axe WCAG 2.2 AA, one h1, heading order, alt text, control names, 24 px targets, no horizontal scroll at 1366/683/341 px, console errors, dead clicks |
