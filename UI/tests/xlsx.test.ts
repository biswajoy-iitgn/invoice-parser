import { describe, expect, it } from 'vitest';
import { csv, serial, xlsx } from '../src/xlsx';
import { cellOf } from '../src/pages/Export';

describe('export files', () => {
  it('turns printed dates into Excel serials', () => {
    expect(serial('21 Aug 2026')).toBe(46255);
    expect(serial('2026-08-21')).toBe(46255);
    expect(serial('not a date')).toBeNull();
  });
  it('keeps amounts and numbers as numbers', () => {
    expect(cellOf('Amount', '9,64,904.88')).toEqual({ money: 964904.88 });
    expect(cellOf('Number', '10669 kg')).toBe('10669 kg');
    expect(cellOf('Date', '21 Aug 2026')).toEqual({ date: '21 Aug 2026' });
  });
  it('makes CSV text that starts a formula literal', async () => {
    const bytes = new Uint8Array(await csv([['=HYPERLINK("x")', '-12.50', '@SUM(A1)', 'Plain']]).arrayBuffer());
    expect([...bytes.slice(0, 3)]).toEqual([0xEF, 0xBB, 0xBF]); // byte-order mark, so Excel reads UTF-8
    expect(new TextDecoder().decode(bytes.slice(3))).toBe('"\'=HYPERLINK(""x"")","-12.50","\'@SUM(A1)","Plain"');
  });
  it('writes a zip that starts with the Office content types', async () => {
    const blob = xlsx([{ name: 'Freight invoice', rows: [['Document', 'Total', 'Date'], ['A.pdf', { money: 1200.5 }, { date: '21 Aug 2026' }]] }]);
    const bytes = new Uint8Array(await blob.arrayBuffer());
    expect([...bytes.slice(0, 4)]).toEqual([0x50, 0x4b, 0x03, 0x04]);
    expect(new TextDecoder().decode(bytes.slice(30, 49))).toBe('[Content_Types].xml');
  });
});
