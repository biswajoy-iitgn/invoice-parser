import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../src/App';
import { ResultView } from '../src/pages/ResultView';
import { api, fromServer, SNAPSHOT, toResult, withServer } from '../src/api';

const GENERAL_RESULT = {
  profile: 'general', document_type: 'certificate', title: 'TEST CERTIFICATE', decision: 'REVIEW', reasons: ['table 1: only 50% of cells found on the page'],
  fields: [{ label: 'Customer', value: 'ACME SRL', status: 'verified', boxes: [{ page: 1, bbox: [1, 2, 3, 4] }] },
           { label: 'Approved by', value: 'Nobody', status: 'unverified' },
           { label: 'C', value: '0,047', status: 'verified', model_read: '0.047' },
           { label: 'Coil', value: '260405)', status: 'unverified', vision_read: '2604051' }],
  tables: [{ title: '', columns: ['Heat', 'C'], rows: [['H1', '0,047']], verified: 0.5, source: 'document-parser' }],
  checks: [{ name: 'table 1: carbon equivalent (CE)', ok: false, detail: 'row 1: CE printed 0.714, the formula gives 0.1748' }, { name: 'table 1: total row', ok: null, detail: 'no total row to check' }],
  text: [{ page: 1, source: 'text-layer', text: 'Customer ACME SRL' }],
};

afterEach(() => vi.unstubAllGlobals());
URL.createObjectURL = () => 'blob:test'; // jsdom has none

describe('service results', () => {
  it('maps a general read and a profile read to one shape', () => {
    const g = toResult(GENERAL_RESULT as never, { reader: 'r', tables: 't' }, 3.2);
    expect(g.fields.map((f) => f.label)).toEqual(['Customer', 'Approved by', 'C', 'Coil']);
    expect(g.fields[0].box).toEqual([1, 2, 3, 4]);
    expect(g.fields[2].modelRead).toBe('0.047');
    expect(g.tables[0].source).toBe('document-parser');
    const p = toResult({
      profile: 'gate_pass', decision: 'REVIEW', reasons: [], fields: { gate_pass_no: { value: 'GP/1', status: 'verified' } },
      tables: { items: { columns: ['Description', 'Qty'], rows: [{ cells: { Description: 'Bearing', Qty: '17' }, verified: 1 }], verified: 1 } },
    } as never);
    expect(p.fields[0].label).toBe('gate pass no');
    expect(p.tables[0].rows).toEqual([['Bearing', '17']]);
  });

  it('adds service profiles the workspace lacks and keeps local edits', () => {
    const mine = fromServer(SNAPSHOT.profiles.find((x) => x.id === 'gate_pass') as never);
    const merged = withServer([{ ...mine, name: 'My gate pass', models: { reader: 'x' } }], SNAPSHOT.profiles);
    expect(merged.find((x) => x.id === 'gate_pass')?.name).toBe('My gate pass');
    expect(merged.find((x) => x.id === 'gate_pass')?.models?.reader).toBe('qwen3-vl-4b');
    expect(merged.map((x) => x.id)).toContain('general');
  });
});

describe('result view', () => {
  const result = toResult(GENERAL_RESULT as never);
  it('shows values, tables and text, and keeps Approve locked until the flagged items are checked', async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(<ResultView result={result} approved={false} onApprove={onApprove} />);
    expect(screen.getByText('The model wrote 0.047; the page prints 0,047')).toBeInTheDocument();
    expect(screen.getAllByText('Not found').length).toBeGreaterThan(0);
    expect(screen.getByText('Re-read from the image: 2604051')).toBeInTheDocument();
    expect(screen.getByText(/only 50% of cells found on the page/)).toBeInTheDocument();
    const approve = screen.getByRole('button', { name: 'Approve' });
    expect(approve).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: /I checked the 2 flagged items / }));
    await user.click(approve);
    expect(onApprove).toHaveBeenCalled();
    await user.click(screen.getByRole('tab', { name: 'Tables' }));
    expect(within(screen.getByRole('table', { name: 'Table 1' })).getByText('0,047')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Checks' }));
    expect(screen.getByText('row 1: CE printed 0.714, the formula gives 0.1748')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('Not run')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Text' }));
    expect(screen.getByRole('textbox', { name: 'Text of page 1' })).toHaveValue('Customer ACME SRL');
  });
});

describe('profiles from the service', () => {
  it('lists every use case, and changing a model is one choice and a save', async () => {
    const user = userEvent.setup();
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (init?.method === 'PUT') return new Response(JSON.stringify({ ...SNAPSHOT.profiles.find((x) => x.id === 'general'), models: { reader: 'qwen3-vl-4b', tables: 'off' } }), { status: 200 });
      return new Response('offline', { status: 503 });
    }));
    location.hash = '#/profiles';
    render(<App />);
    for (const name of ['Gate pass slip', 'Quality certificate / test report', 'Weekly staff roster', 'Any document']) expect(await screen.findByRole('heading', { name })).toBeInTheDocument();
    location.hash = '#/profile/general';
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    // a profile that reads everything opens on Reading, where the models are
    await user.click(await screen.findByRole('combobox', { name: 'Table reader' }));
    await user.click(await screen.findByRole('option', { name: 'No tables' }));
    await user.click(screen.getAllByRole('button', { name: 'Save' })[0]);   // the page's and the unsaved-changes bar's
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'PUT' && c.url === '/api/profiles/general/models')).toBe(true));
    const put = calls.find((c) => c.init?.method === 'PUT');
    expect(JSON.parse(put?.init?.body as string)).toEqual({ reader: 'qwen3-vl-4b', tables: 'off' });
  });

  it('reads an uploaded file through the service and opens it for review with its tables', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST' && url === '/api/profiles/general/extract') return new Response(JSON.stringify({ result: GENERAL_RESULT, audit: {}, seconds: 4.2 }), { status: 200 });
      return new Response('offline', { status: 503 });
    }));
    location.hash = '#/extract';
    render(<App />);
    await screen.findByRole('heading', { level: 1, name: 'Extract' });
    const input = document.querySelector<HTMLInputElement>('input[type=file][multiple]') as HTMLInputElement;
    await user.upload(input, new File(['%PDF-1.4'], 'certificate-from-a-customer.pdf', { type: 'application/pdf' }));
    await waitFor(() => expect(location.hash).toMatch(/^#\/run\/R-/));
    expect((await screen.findAllByText('certificate-from-a-customer.pdf')).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getAllByText(/review/i).length).toBeGreaterThan(0));
  });
});

describe('review fixes', () => {
  it('labels a value read with low confidence as such, not as missing from the page', () => {
    const r = toResult({ ...GENERAL_RESULT, fields: [{ label: 'Coil', value: '260405)', status: 'unverified', issue: 'read with low OCR confidence (0.93); check it on the page' }] } as never);
    render(<ResultView result={r} approved={false} onApprove={() => {}} />);
    expect(screen.getByText('Low confidence')).toBeInTheDocument();
    expect(screen.queryByText('Not found')).toBeNull();
  });

  it('shows a validation error from the service as text', async () => {
    vi.stubGlobal('fetch', async () => new Response(JSON.stringify({ detail: [{ msg: 'field required' }] }), { status: 422 }));
    await expect(api.models()).rejects.toThrow('field required');
  });
});

