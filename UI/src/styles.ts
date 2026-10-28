// Fluent customisations, the supported way: makeStyles classes passed to a component's className or slots
// (Fluent merges them last, so no specificity tricks), and Fluent's exported class names where a slot sits inside.
// Layout of the app's own elements stays in index.css.
import {
  buttonClassNames, fluentProviderClassNames, listboxClassNames, makeStaticStyles, makeStyles, menuPopoverClassNames,
  popoverSurfaceClassNames, shorthands, tokens,
} from '@fluentui/react-components';

const ACRYLIC = 'blur(30px) saturate(150%)';
const SURFACES = [menuPopoverClassNames.root, popoverSurfaceClassNames.root, listboxClassNames.root]
  .map((c) => `.${fluentProviderClassNames.root} .${c}`).join(', ');

/** Page-level rules: the provider fills the page; menus, popovers and dropdown lists are acrylic (they render in portals). */
export const useStaticStyles = makeStaticStyles(`
  #root > .${fluentProviderClassNames.root} { height: 100%; }
  ${SURFACES} { background-color: var(--appAcrylic); -webkit-backdrop-filter: ${ACRYLIC}; backdrop-filter: ${ACRYLIC}; }
  @supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) { ${SURFACES} { background-color: var(--appSurface); } }
  @media (prefers-reduced-transparency: reduce) { ${SURFACES} { background-color: var(--appSurface); -webkit-backdrop-filter: none; backdrop-filter: none; } }
`);

export const useShellStyles = makeStyles({
  // Buttons on the blue suite header: white at rest, a darker header on hover, so white stays above 4.5:1.
  // Fluent tints subtle-button icons brand blue on hover, which would vanish on the blue bar.
  headerButton: {
    color: '#FFFFFF',
    [`& .${buttonClassNames.icon}`]: { color: '#FFFFFF' },
    ':hover': { backgroundColor: 'rgba(0, 27, 55, .32)', color: '#FFFFFF', [`& .${buttonClassNames.icon}`]: { color: '#FFFFFF' } },
    ':hover:active': { backgroundColor: 'rgba(0, 27, 55, .48)', color: '#FFFFFF', [`& .${buttonClassNames.icon}`]: { color: '#FFFFFF' } },
    ':focus-visible': { outlineColor: '#FFFFFF', outlineStyle: 'solid', outlineWidth: '2px', outlineOffset: '-2px' },
  },
  // Rail and count buttons let their count badge sit outside the button's box.
  countButton: { overflow: 'visible' },
  railIcon: { margin: 0 },
  toast: {
    backgroundColor: 'var(--appAcrylic)',
    backdropFilter: ACRYLIC,
    '@media (prefers-reduced-transparency: reduce)': { backgroundColor: 'var(--appSurface)', backdropFilter: 'none' },
  },
});

export const useCardStyles = makeStyles({
  fileHeader: { ...shorthands.padding('12px', '14px') },
  // actions line up across a row of cards whose text wraps differently
  footer: { marginTop: 'auto' },
});

export const useControlStyles = makeStyles({
  // A link inside a table row: wraps long names, and keeps a 24 px target (WCAG 2.5.8) inside the clickable row.
  cellLink: { overflowWrap: 'anywhere', textAlign: 'start', minWidth: 0, minHeight: '24px' },
  // The whole 32 px slider is the target, not the 20 px thumb row.
  sliderInput: { height: '100%' },
  fullWidth: { width: '100%' },
  insightItem: {
    display: 'flex', alignItems: 'center', gap: '12px', ...shorthands.padding('10px', '8px'), borderRadius: tokens.borderRadiusMedium, cursor: 'pointer',
    ':hover': { backgroundColor: tokens.colorNeutralBackground1Hover },
  },
  queueList: { display: 'flex', flexDirection: 'column', gap: '2px' },
  // Field table columns sized to their controls; the header hides when the table reflows to cards.
  colCheck: { width: '88px' },
  colType: { width: '160px' },
  colSwitch: { width: '120px' },
  // Fluent charts capitalise legend text; Fluent web copy is sentence case.
  legendText: { textTransform: 'none' },
});
