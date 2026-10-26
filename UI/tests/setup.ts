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
