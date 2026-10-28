// Hash chain over the audit log, oldest first: each hash covers the previous hash and the entry, so changing or
// removing an entry breaks every hash after it. The service keeps the authoritative chain (database/audit.py).
import type { AuditEntry } from './types';

const hex = (b: ArrayBuffer) => [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, '0')).join('');
// Browsers expose crypto.subtle only on HTTPS or localhost: served over plain HTTP the browser's own log stays unchained
// ('' hashes, so Verify stays off) instead of failing. The service's chain does not depend on this.
export const hashOf = async (prev: string, e: Omit<AuditEntry, 'hash'>): Promise<string> =>
  globalThis.crypto?.subtle ? hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode([prev, e.at, e.actor, e.action, e.target].join('␟')))) : '';

/** Entries newest first, as shown, with every hash computed. */
export async function chain(entries: AuditEntry[]): Promise<AuditEntry[]> {
  const out: AuditEntry[] = [];
  let prev = '';
  for (const e of [...entries].reverse()) {
    const hash = await hashOf(prev, e);
    out.unshift({ ...e, hash });
    prev = hash;
  }
  return out;
}

/** The newest-first index of the oldest entry whose hash doesn't match, or -1 when the chain is intact. */
export async function verify(entries: AuditEntry[]): Promise<number> {
  let prev = '';
  for (let i = entries.length - 1; i >= 0; i--) {
    if (await hashOf(prev, entries[i]) !== entries[i].hash) return i;
    prev = entries[i].hash;
  }
  return -1;
}

/** "25 Oct, 14:03:12", the log's own time format. */
export const stamp = (d = new Date()): string =>
  `${d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })}, ${d.toLocaleTimeString('en-GB', { hour12: false })}`;
