// Domain model shared by every page. Mirrors what the extraction service returns (see ../DESIGN.md).
import type { Dispatch, SetStateAction } from 'react';
import type { EngineState } from './engines';

export type FieldType = 'Text' | 'Long text' | 'Date' | 'Number' | 'Amount' | 'Yes/No' | 'Table';
export type Box = [x0: number, y0: number, x1: number, y1: number];
export type DocKind = 'vector' | 'scan';
export type Values = Record<string, string>;

// A sample document: real pages (redacted) and the values read from them, each pointing at where it is printed.
export interface SampleField { key: string; value: string; conf: number; page: number; box: Box | null; note?: string }
export interface SamplePage { src: string; w: number; h: number; thumb?: string }
export interface SampleDoc { id: string; name: string; vendor: string; kind: DocKind; pages: SamplePage[]; fields: SampleField[]; twin?: string }

// A profile: what kind of document to expect, which fields to read, where results go. Any kind of document:
// a template (invoice, contract, letter, datasheet …) only seeds the fields.
export interface ProfileField {
  key: string; label: string; type: FieldType; group: string; enabled: boolean; required: boolean;
  /** Tells the AI where or how to find the value. */
  hint?: string;
  /** Column names of a Table field (one row per line item, party, specification …). */
  columns?: string[];
}
export type Preset = 'fast' | 'balanced' | 'accurate';
/** What reads a profile's documents: the invoice reader (fixed fields), field-bound reading, or everything printed. */
export type ReadEngine = 'invoice_v2' | 'generic' | 'general';
export interface ProfileModels { reader?: string; tables?: string }
export type ExportTarget = 'excel' | 'csv' | 'database';
export interface Profile {
  id: string; name: string; docType: string; template: string; preset: Preset; evaluator: boolean; onPremOnly: boolean;
  linked: string[]; languages: string[]; exports: ExportTarget[]; owner: string; fields: ProfileField[];
  /** The extraction service's profile (configs/profiles/*.yaml) this one reads with, and the models it chose. */
  serverId?: string; engine?: ReadEngine; models?: ProfileModels; editableModels?: (keyof ProfileModels)[];
}

/** What the service returns for one document read under a profile (invoice_extractor/profiles.py). */
export interface ResultField { label: string; value: string; status: 'verified' | 'unverified' | 'missing' | 'invalid'; issue?: string; modelRead?: string; visionRead?: string; box?: Box; page?: number }
export interface ResultTable { title: string; columns: string[]; rows: string[][]; verified: number | null; source: string }
/** One arithmetic check: ok is null when it could not run (a value was missing or not a number). */
export interface ResultCheck { name: string; ok: boolean | null; detail: string }
export interface ProfileResult {
  profile: string; documentType: string; title: string; decision: 'REVIEW' | 'ACCEPT'; reasons: string[];
  fields: ResultField[]; tables: ResultTable[]; checks: ResultCheck[]; text: { page: number; source: string; text: string }[];
  models?: { reader: string; tables: string }; seconds?: number;
  /** Which profile read it and with what, when the service chose (profile "auto"). */
  route?: { profile: string; detected: boolean; reader: string; tables: string; why: string[] };
}

// Batches and the documents in them.
export type DocStatus = 'queued' | 'running' | 'review' | 'accepted' | 'exported' | 'failed';
export interface RunDoc {
  id: string; name: string; pages: number; kind: DocKind; profile: string; route: number | null; status: DocStatus; score: number | null;
  seconds?: number; edited?: boolean; day?: string; flagged?: string[]; values?: Values; archived?: boolean;
  sample?: string | null; step?: number; final?: DocStatus; reason?: string;
  /** A document read by the service: every value, table and the text, as printed. Set instead of a sample. */
  result?: ProfileResult;
  /** Being read by the service, not stepped through the sample pipeline. */
  live?: boolean;
  /** Who approved it and which flagged values they checked or corrected. */
  approval?: { by: string; at: string; checked: string[]; changed: string[] };
}
export type RunStatus = 'running' | 'review' | 'done';
export interface Run { id: string; profile: string; source: string; started: string; by: string; status: RunStatus; day?: string; docs: RunDoc[] }
/** A document with the batch it came from. */
export interface Doc extends RunDoc { run: string; runProfile: string }
/** A document with its pages, its values (as read, then as edited) and the uploaded file while this visit lasts. */
export interface DocRecord extends Omit<Doc, 'sample' | 'values'> { sample?: SampleDoc; blob?: { url: string; type: string }; values: Values }

// Databases the results are exported to.
export interface Connection { id: string; name: string; kind: string; host: string; database: string; status: 'connected' | 'offline' | 'untested' }
export type Column = [name: string, type: string, key: string];
export interface TableDef { columns: Column[]; rows: string[][]; count?: number | null; loaded?: boolean }
export type Tables = Record<string, Record<string, TableDef>>;

// Automations.
export type When = 'review' | 'accepted' | 'complete';
export type Action = 'webhook' | 'notify' | 'approval';
export interface Workflow { id: string; name: string; when: When; action: Action; target: string; profiles: 'all' | string[]; body?: string; enabled: boolean; last: string }

// People, audit and updates.
export type Role = 'admin' | 'user';
export interface User { id: string; name: string; email: string; role: Role; groups: string[]; last: string }
export interface AuditEntry { at: string; actor: string; action: string; target: string; hash: string }

// Notifications.
export type NoteKind = 'approval' | 'run' | 'insight' | 'engine';
export interface Note { id: string; kind: NoteKind; title: string; body?: string; time: string; page: [string, string?]; admin?: boolean }

export type Intent = 'success' | 'info' | 'warning' | 'error';
export interface Route { page: string; id?: string }
export interface UploadItem { name: string; file?: File; sampleId?: string }

type Setter<T> = Dispatch<SetStateAction<T>>;
export interface AppContextValue {
  route: Route;
  go: (page: string, id?: string) => void;
  role: Role;
  profiles: Profile[]; setProfiles: Setter<Profile[]>;
  runs: Run[]; setRuns: Setter<Run[]>;
  tables: Tables; setTables: Setter<Tables>;
  workflows: Workflow[]; setWorkflows: Setter<Workflow[]>;
  notify: (title: string, intent?: Intent, body?: string) => void;
  pushNote: (note: Omit<Note, 'id' | 'time'>) => void;
  startRun: (items: UploadItem[], forced: string | null, source: string) => void;
  reviewCount: number;
  allDocs: Doc[];
  docRecord: (id: string) => DocRecord | null;
  edits: Record<string, Values>; setEdits: Setter<Record<string, Values>>;
  query: string; setQuery: Setter<string>;
  dark: boolean;
  /** The signed-in person: actor in the audit log and approver of record. */
  me: User;
  engines: EngineState; setEngines: Setter<EngineState>;
  /** Unsaved engine settings, kept while moving between engine pages. */
  engineDraft: EngineState | null; setEngineDraft: Setter<EngineState | null>;
  /** Values read below this confidence wait for a person (AI and engines, Field matching). */
  flagBelow: number;
  audit: AuditEntry[];
  log: (action: string, target: string) => void;
  connections: Connection[]; setConnections: Setter<Connection[]>;
  samples: boolean; removeSamples: () => void;
}
