// The extraction service (invoice_extractor/service.py): profiles, the models they can choose, and reading one file.
// Every call fails soft: offline, the app keeps working on the bundled snapshot (serverProfiles.json) and sample data.
import snapshot from './serverProfiles.json';
import type { Box, Profile, ProfileModels, ProfileResult, ProfileField, FieldType, ReadEngine, ResultField, ResultTable, Role, RunDoc } from './types';

export interface ModelEntry { id: string; name: string; licence?: string; commercial: boolean; default?: boolean; note?: string }
export interface ModelCatalog { readers: ModelEntry[]; tables: ModelEntry[] }
interface ServerField { key: string; label: string; type: string; required: boolean; hint?: string }
interface ServerTable { key: string; label: string; columns: string[]; required: boolean; hint?: string }
export interface ServerProfile {
  id: string; name: string; description?: string; engine: ReadEngine; fields: ServerField[]; tables: ServerTable[];
  models: Required<ProfileModels>; editable_models: (keyof ProfileModels)[];
}

const BASE = import.meta.env.VITE_API_BASE ?? '';
const TYPES: Record<string, FieldType> = { text: 'Text', id: 'Text', choice: 'Text', date: 'Date', number: 'Number', amount: 'Amount' };

/** Fired when the service says the session is gone (expired, signed out elsewhere): the app shows sign-in. */
export const SIGNED_OUT = 'dx:signed-out';

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { credentials: 'same-origin', ...init });
  if (r.status === 401 && !path.startsWith('/api/auth/')) window.dispatchEvent(new Event(SIGNED_OUT));
  if (!r.ok) {
    // FastAPI sends detail as a string, or as a list of {msg} for a request that doesn't validate
    const detail = (await r.json().catch(() => null))?.detail;
    const text = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d) => d?.msg ?? '').filter(Boolean).join('; ') : '';
    throw new Error(text || `${r.status} ${r.statusText}`);
  }
  return r.json() as Promise<T>;
}

/** A service profile as the app's profile: its fields and tables become fields, its models are carried along. */
export function fromServer(d: ServerProfile): Profile {
  const fields: ProfileField[] = [
    ...d.fields.map((f): ProfileField => ({ key: f.key, label: f.label, type: TYPES[f.type] ?? 'Text', group: 'Fields', enabled: true, required: f.required, ...(f.hint && { hint: f.hint }) })),
    ...d.tables.map((t): ProfileField => ({ key: t.key, label: t.label, type: 'Table', group: 'Tables', enabled: true, required: t.required, columns: t.columns, ...(t.hint && { hint: t.hint }) })),
  ];
  return {
    id: d.id, serverId: d.id, name: d.name, docType: d.description || d.name, template: 'blank', preset: 'balanced', evaluator: true, onPremOnly: true,
    linked: [], languages: ['English'], exports: ['excel'], owner: '', fields, engine: d.engine, models: d.models, editableModels: d.editable_models,
  };
}

/** Bundled copy of GET /api/profiles and /api/models (npm run profiles:export), for when the service can't be reached. */
export const SNAPSHOT = snapshot as unknown as { profiles: ServerProfile[]; models: ModelCatalog };

export const api = {
  profiles: () => call<{ profiles: ServerProfile[] }>('/api/profiles').then((r) => r.profiles),
  models: () => call<ModelCatalog>('/api/models'),
  saveModels: (id: string, models: ProfileModels) => call<ServerProfile>(`/api/profiles/${encodeURIComponent(id)}/models`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(models),
  }),
  /** Files go to the service as one batch: it reads them a few at a time (its reader slots) and keeps each result. */
  batch: {
    /** Start a batch (no id) or add to one; the answer lists the files in the order they were sent. */
    send: (profile: string, files: File[], id?: string) => {
      const body = new FormData();
      files.forEach((f) => body.append('files', f, f.name));
      body.append('profile', profile);
      return call<BatchStatus & { batch_id?: string; accepted: BatchAccepted[] }>(id ? `/api/batches/${encodeURIComponent(id)}/files` : '/api/batches', { method: 'POST', body });
    },
    status: (id: string) => call<BatchStatus>(`/api/batches/${encodeURIComponent(id)}`),
    result: async (id: string, item: string): Promise<ProfileResult> => {
      const r = await call<{ result: RawResult; audit?: { models?: ProfileResult['models'] }; seconds?: number }>(`/api/batches/${encodeURIComponent(id)}/items/${encodeURIComponent(item)}`);
      return toResult(r.result, r.audit?.models, r.seconds);
    },
  },
};

export interface EnginePackage { name: string; role: string | null; current: string | null; latest: string | null; update_available?: boolean; note?: string; error?: string }
export interface UpdateJob { running: boolean; packages: string[]; returncode: number | null; error: string }
export interface UpdatesReport { packages: EnginePackage[]; auto_update: boolean; job: UpdateJob }
export interface ServerAuditEntry { seq: number; created_at: string; user_id: string | null; entity: string; entity_id: string; action: string; row_hash: string }
export interface ChainCheck { intact: boolean; entries: number; checked: number; first_altered_entry: number | null }

/** Engine versions and installs, and the database's tamper-evident audit log (409 when no database is connected). */
export const service = {
  updates: (refresh = false) => call<UpdatesReport>(`/api/updates${refresh ? '?refresh=1' : ''}`),
  install: (packages: string[]) => call<{ started: boolean; note: string }>('/api/updates/apply', post({ packages })),
  autoUpdate: (enabled: boolean) => call<{ auto_update: boolean }>('/api/updates/auto', post({ enabled })),
  dbStatus: () => call<{ configured: boolean; reachable?: boolean; organization?: string | null; host?: string | null; error?: string; note?: string }>('/api/db/status'),
  dbSchema: () => call<{ tables: { name: string; columns: { name: string; type: string }[]; rows: number | null }[]; links: { table: string; column: string; ref_table: string; ref_column: string }[] }>('/api/db/schema'),
  dbTable: (name: string) => call<{ columns: string[]; rows: (string | null)[][] }>(`/api/db/tables/${encodeURIComponent(name)}`),
  dbConfigure: (url: string, org: string) => call<{ saved: boolean; note?: string }>('/api/db/configure', post({ url, org })),
  auditEntries: (before?: number) => call<{ entries: ServerAuditEntry[]; more: boolean }>(`/api/audit/entries${before ? `?before=${before}` : ''}`),
  verifyChain: () => call<ChainCheck>('/api/audit/verify'),
};
const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

/** A person as the service knows them. The role here is what the server enforces. */
export interface Account { id: string; email: string; name: string; role: Role; must_change: boolean; disabled?: boolean; last_login?: number | null }
export interface Me { user: Account | null; sign_in: boolean; needs_setup: boolean }
export const account = {
  me: () => call<Me>('/api/auth/me'),
  login: (email: string, password: string) => call<{ user: Account }>('/api/auth/login', post({ email, password })),
  logout: () => call<unknown>('/api/auth/logout', post({})),
  password: (old: string, next: string) => call<{ user: Account }>('/api/auth/password', post({ old, new: next })),
  setup: (email: string, name: string, password: string) => call<{ user: Account }>('/api/auth/setup', post({ email, name, password })),
  users: () => call<{ users: Account[] }>('/api/users'),
  add: (u: { email: string; name: string; role: Role; password: string }) => call<Account>('/api/users', post(u)),
  update: (id: string, patch: { role?: Role; disabled?: boolean }) => call<Account>(`/api/users/${encodeURIComponent(id)}`, { ...post(patch), method: 'PATCH' }),
  resetPassword: (id: string, password: string) => call<unknown>(`/api/users/${encodeURIComponent(id)}/password`, post({ password })),
};

export interface BatchItem { id: string; filename: string; status: 'queued' | 'processing' | 'done' | 'failed' | 'cancelled'; error: string }
export interface BatchAccepted { filename: string; id?: string; status?: string; error?: string }
export interface BatchStatus { id: string; finished: boolean; items: BatchItem[] }

// ---- the service's result, in the shape every screen reads ----
interface RawBox { page: number; bbox: Box }
interface RawField { value: string; status: ResultField['status']; issue?: string; model_read?: string; vision_read?: string; boxes?: RawBox[] }
interface RawResult {
  profile: string; decision: 'REVIEW' | 'ACCEPT'; reasons: string[]; document_type?: string; title?: string;
  // a general read lists its fields; a profile read keys them by name
  fields: RawField[] | Record<string, RawField>; tables: unknown; text?: ProfileResult['text']; checks?: ProfileResult['checks'];
  route?: ProfileResult['route'];
}
const box = (f: RawField) => (f.boxes?.[0] ? { box: f.boxes[0].bbox, page: f.boxes[0].page } : {});
const named = (f: RawField, label: string): ResultField => ({ label, value: f.value, status: f.status, ...(f.issue && { issue: f.issue }), ...(f.model_read && { modelRead: f.model_read }), ...(f.vision_read && { visionRead: f.vision_read }), ...box(f) });

export function toResult(r: RawResult, models?: ProfileResult['models'], seconds?: number): ProfileResult {
  const fields = Array.isArray(r.fields)
    ? r.fields.map((f) => named(f, (f as RawField & { label: string }).label))
    : Object.entries(r.fields).map(([k, f]) => named(f, k.replace(/_/g, ' ')));
  // profile reads key their tables by name, rows as {cells, verified}; general reads list them
  const tables: ResultTable[] = Array.isArray(r.tables)
    ? (r.tables as ResultTable[]).map((t) => ({ title: t.title ?? '', columns: t.columns, rows: t.rows, verified: t.verified, source: t.source ?? 'reader' }))
    : Object.entries(r.tables as Record<string, { columns: string[]; rows: { cells: Record<string, string> }[]; verified: number | null }>).map(([k, t]) => ({
      title: k.replace(/_/g, ' '), columns: t.columns, rows: t.rows.map((x) => t.columns.map((c) => x.cells[c] ?? '')), verified: t.verified, source: 'reader',
    }));
  return {
    profile: r.profile, documentType: r.document_type ?? '', title: r.title ?? '', decision: r.decision, reasons: r.reasons, fields, tables,
    checks: r.checks ?? [], text: r.text ?? [], ...(models && { models }), ...(seconds !== undefined && { seconds }), ...(r.route && { route: r.route }),
  };
}

/** Profiles the workspace already has, plus any service profile it lacks; service profiles refresh their models and fields. */
export function withServer(list: Profile[], server: ServerProfile[]): Profile[] {
  const known = new Set(list.flatMap((p) => [p.id, p.serverId ?? p.id]));
  const fresh = new Map(server.map((d) => [d.id, fromServer(d)]));
  return [
    ...list.map((p) => {
      const f = p.serverId ? fresh.get(p.serverId) : undefined;
      return f ? { ...p, engine: f.engine, models: f.models, editableModels: f.editableModels } : p;
    }),
    ...server.filter((d) => !known.has(d.id)).map((d) => fromServer(d)),
  ];
}

// What a document's row shows once the service has read it.
export function readPatch(result: ProfileResult, autoAccept: boolean): Partial<RunDoc> {
  const checked = result.fields.length ? result.fields.filter((f) => f.status === 'verified').length / result.fields.length : 1;
  return {
    status: result.decision === 'ACCEPT' && autoAccept ? 'accepted' : 'review', result, score: +checked.toFixed(2), seconds: result.seconds,
    values: Object.fromEntries(result.fields.map((f) => [f.label, f.value])), flagged: result.fields.filter((f) => f.status !== 'verified').map((f) => f.label),
  };
}

// Real files go to the service as one batch, CHUNK files per request; the batch is then polled until every file has an answer.
// The service reads a few at a time (its reader slots), so a folder of hundreds never overloads it. Each document settles once.
const CHUNK = 10;
const POLL_MS = 1500;
const GIVE_UP = 40; // status checks in a row that fail (about a minute) before the rest are marked failed
export async function readOnService(profile: string, docs: { doc: RunDoc; file: File }[], settle: (id: string, patch: Partial<RunDoc>) => void, autoAccept: boolean, pollMs = POLL_MS) {
  const docOf = new Map<string, string>(); // batch item id -> document id
  let batch = '';
  for (let i = 0; i < docs.length; i += CHUNK) {
    const part = docs.slice(i, i + CHUNK);
    try {
      const r = await api.batch.send(profile, part.map((x) => x.file), batch || undefined);
      batch ||= r.batch_id ?? r.id;
      r.accepted.forEach((a, k) => (a.id ? docOf.set(a.id, part[k].doc.id) : settle(part[k].doc.id, { status: 'failed', reason: a.error || "Couldn't read this file" })));
    } catch (e) {
      part.forEach((x) => settle(x.doc.id, { status: 'failed', reason: (e as Error).message || "Couldn't reach the service" }));
    }
  }
  const open = new Set(docOf.keys());
  for (let misses = 0; open.size && batch;) {
    await new Promise((r) => setTimeout(r, pollMs));
    let st: BatchStatus;
    try { st = await api.batch.status(batch); misses = 0; } catch {
      if (++misses < GIVE_UP) continue;
      open.forEach((it) => settle(docOf.get(it) as string, { status: 'failed', reason: 'Lost contact with the service' }));
      return;
    }
    for (const it of st.items) {
      if (!open.has(it.id) || it.status === 'queued' || it.status === 'processing') continue;
      open.delete(it.id);
      const docId = docOf.get(it.id) as string;
      if (it.status !== 'done') { settle(docId, { status: 'failed', reason: it.error || 'Cancelled' }); continue; }
      try { settle(docId, readPatch(await api.batch.result(batch, it.id), autoAccept)); } catch (e) { settle(docId, { status: 'failed', reason: (e as Error).message }); }
    }
  }
}
