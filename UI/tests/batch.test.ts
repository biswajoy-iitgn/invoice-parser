import { afterEach, describe, expect, it, vi } from 'vitest';
import { readOnService } from '../src/api';
import type { RunDoc } from '../src/types';

afterEach(() => vi.unstubAllGlobals());

const doc = (id: string) => ({ id, name: `${id}.pdf`, pages: 1, kind: 'vector', profile: 'freight', route: null, status: 'running', score: null } as RunDoc);
const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

describe('reading files on the service', () => {
  it('sends files in chunks to one batch, then settles each document once from the batch status', async () => {
    const sent: { url: string; files: number; profile: string }[] = [];
    let polls = 0;
    const result = { profile: 'logistics_invoice', decision: 'REVIEW', reasons: [], fields: { 'Invoice No': { value: 'INV-1', status: 'verified' } }, tables: {}, checks: [], text: [] };
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        const body = init.body as FormData;
        const files = body.getAll('files').length;
        sent.push({ url, files, profile: String(body.get('profile')) });
        const first = sent.length === 1 ? 0 : 10;
        return ok({ id: 'b1', ...(url === '/api/batches' && { batch_id: 'b1' }), finished: false, items: [],
          accepted: Array.from({ length: files }, (_, k) => (first + k === 3 ? { filename: 'x', error: 'unsupported file type' } : { filename: 'x', id: `i${first + k}` })) });
      }
      if (url === '/api/batches/b1') {
        polls += 1;
        const items = Array.from({ length: 12 }, (_, k) => ({ id: `i${k}`, filename: 'x', error: k === 5 ? 'RuntimeError: reader down' : '',
          status: polls < 2 ? 'queued' : k === 5 ? 'failed' : 'done' })).filter((x) => x.id !== 'i3');
        return ok({ id: 'b1', finished: polls >= 2, items });
      }
      return ok({ result, audit: { models: { reader: 'r', tables: 't' } }, seconds: 2.5 });
    });
    const docs = Array.from({ length: 12 }, (_, k) => ({ doc: doc(`d${k}`), file: new File(['%PDF'], `d${k}.pdf`) }));
    const settled = new Map<string, Partial<RunDoc>[]>();
    await readOnService('logistics_invoice', docs, (id, p) => settled.set(id, [...(settled.get(id) ?? []), p]), false, 1);
    expect(sent.map((s) => [s.url, s.files, s.profile])).toEqual([['/api/batches', 10, 'logistics_invoice'], ['/api/batches/b1/files', 2, 'logistics_invoice']]);
    expect(settled.size).toBe(12);
    expect([...settled.values()].every((p) => p.length === 1)).toBe(true);
    expect(settled.get('d3')?.[0]).toMatchObject({ status: 'failed', reason: 'unsupported file type' });
    expect(settled.get('d5')?.[0]).toMatchObject({ status: 'failed', reason: 'RuntimeError: reader down' });
    expect(settled.get('d0')?.[0]).toMatchObject({ status: 'review', score: 1, seconds: 2.5, values: { 'Invoice No': 'INV-1' } });
  });

  it('marks every document failed when the service cannot be reached', async () => {
    vi.stubGlobal('fetch', async () => { throw new TypeError('Failed to fetch'); });
    const settled: string[] = [];
    await readOnService('general', [{ doc: doc('a'), file: new File(['x'], 'a.pdf') }], (_, p) => settled.push(String(p.reason)), false, 1);
    expect(settled).toEqual(['Failed to fetch']);
  });
});
