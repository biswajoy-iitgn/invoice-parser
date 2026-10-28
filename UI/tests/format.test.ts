import { describe, expect, it } from 'vitest';
import { count, money, pct } from '../src/format';

describe('format', () => {
  it('picks the plural form from the locale', () => {
    expect(count(1, 'document', 'documents')).toBe('1 document');
    expect(count(0, 'document', 'documents')).toBe('0 documents');
    expect(count(1200, 'row', 'rows')).toBe('1,200 rows');
  });
  it('groups money the Indian way', () => expect(money(964904.88)).toBe('9,64,904.88'));
  it('rounds confidence to a whole percent', () => {
    expect(pct(0.814)).toBe('81%');
    expect(pct(null)).toBe('');
  });
});
