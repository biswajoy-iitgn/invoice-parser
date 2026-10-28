// jsdom lacks the layout and media APIs the app reads; these stand in with a desktop-sized, light-theme window.
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => { cleanup(); localStorage.clear(); location.hash = ''; });

window.matchMedia = (query: string) => ({
  matches: false, media: query, onchange: null,
  addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
});
Element.prototype.scrollTo = () => {};
Element.prototype.scrollIntoView = () => {};
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
// jsdom has no layout: offsetParent is always null and <body> measures 0 x 0. Fluent's focus manager (tabster Focusable.isVisible)
// then takes every control for hidden, a dialog finds nothing to focus, and its modal hides the dialog itself (aria-hidden)
// a moment after it opens, so a test that queries the dialog a little late fails.
// ponytail: an attached element counts as shown; display:none ancestors are not checked (no test needs it).
Object.defineProperty(HTMLElement.prototype, 'offsetParent', {
  configurable: true,
  get(this: HTMLElement) { return this.isConnected && this !== document.body ? this.parentElement : null; },
});
document.body.getBoundingClientRect = () => new DOMRect(0, 0, 1366, 768);
