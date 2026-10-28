import { describe, expect, it } from 'vitest';
import { chain, verify } from '../src/audit';

const entries = [
  { at: '24 Oct, 10:00:00', actor: 'A', action: 'Approved document', target: 'X.pdf', hash: '' },
  { at: '24 Oct, 09:00:00', actor: 'B', action: 'Edited field', target: 'Y.pdf', hash: '' },
  { at: '23 Oct, 18:00:00', actor: 'C', action: 'Exported to database', target: 'invoices', hash: '' },
];

describe('audit chain', () => {
  it('verifies an untouched log', async () => {
    expect(await verify(await chain(entries))).toBe(-1);
  });
  it('finds the entry that was changed', async () => {
    const log = await chain(entries);
    log[1] = { ...log[1], target: 'Z.pdf' };
    expect(await verify(log)).toBe(1);
  });
  it('notices a removed entry', async () => {
    const log = await chain(entries);
    expect(await verify([log[0], log[2]])).toBe(0);
  });
});
