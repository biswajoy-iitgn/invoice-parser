import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from '../src/App';

describe('app', () => {
  it('moves between pages from the rail and updates the address', async () => {
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByRole('heading', { level: 1 })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^Documents/ }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Documents' })).toBeInTheDocument();
    expect(location.hash).toBe('#/library');
  });

  it('shows extracted data as a sheet with one tab stop and arrow-key movement', async () => {
    const user = userEvent.setup();
    location.hash = '#/library/data';
    render(<App />);
    const grid = await screen.findByRole('grid', { name: 'Freight invoice sheet' });
    const stops = within(grid).getAllByRole('gridcell').concat(within(grid).getAllByRole('columnheader')).filter((c) => c.tabIndex === 0);
    expect(stops).toHaveLength(1);
    stops[0].focus();
    await user.keyboard('{ArrowDown}{ArrowRight}');
    expect(document.activeElement).not.toBe(stops[0]);
    expect(document.activeElement?.getAttribute('tabindex')).toBe('0');
  });

  it('keeps Approve unavailable, with the reason, until every flagged value is checked', async () => {
    const user = userEvent.setup();
    location.hash = '#/review/doc-dachser';
    render(<App />);
    const approve = await screen.findByRole('button', { name: 'Approve' });
    expect(approve).toHaveAttribute('aria-disabled', 'true');
    expect(approve).toHaveAccessibleDescription('Check 2 values to approve');
    for (const b of screen.getAllByRole('button', { name: 'Looks right' })) await user.click(b);
    expect(screen.getByRole('button', { name: 'Approve' })).not.toHaveAttribute('aria-disabled');
  });

  it('edits a database cell like Excel and keeps it unsaved until Save', async () => {
    const user = userEvent.setup();
    location.hash = '#/data';
    render(<App />);
    const grid = await screen.findByRole('grid', { name: 'vendors table' });
    const cell = within(grid).getAllByRole('gridcell').find((c) => c.textContent === 'Ahmedabad');
    if (!cell) throw new Error('no city cell');
    cell.focus();
    await user.keyboard('{Enter}');
    const input = within(grid).getByRole('textbox');
    await user.clear(input);
    await user.type(input, 'Vadodara{Enter}');
    expect(await screen.findByText('1 change to vendors')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(within(grid).getAllByRole('gridcell').filter((c) => c.textContent === 'Vadodara')).toHaveLength(1);
  });

  it('creates a profile for any kind of document and adds a field to it', async () => {
    const user = userEvent.setup();
    location.hash = '#/profiles';
    render(<App />);
    await user.click(await screen.findByRole('button', { name: 'New profile' }));
    await user.type(screen.getByRole('textbox', { name: /^Name/ }), 'Mill test certificate');
    await user.click(screen.getByRole('radio', { name: "No fields, I'll add my own" }));
    await user.click(screen.getByRole('button', { name: 'Create' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Mill test certificate' })).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: 'Add field' })[0]);
    await user.type(await screen.findByRole('textbox', { name: /^Field name/ }), 'Heat number');
    await user.click(screen.getByRole('button', { name: 'Add' }));
    expect(await screen.findByText('Heat number')).toBeInTheDocument();
    expect(screen.getByText('1 of 1 field included')).toBeInTheDocument();
  });

  it('pages long lists instead of rendering every row', async () => {
    location.hash = '#/library';
    render(<App />);
    const pages = await screen.findByRole('navigation', { name: 'Pages of documents' });
    expect(within(pages).getByText(/^Page 1 of \d+$/)).toBeInTheDocument();
    expect(within(pages).getByText(/^1 to 50 of \d+ documents$/)).toBeInTheDocument();
  });
});
