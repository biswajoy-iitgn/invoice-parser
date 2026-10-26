// Harsha Fluent theme. Ramps are the exact HDS.PALETTE OKLCH scales
// (HarshaLMS_V2/tools/design/hds/src/palette.js); slot 80 = Harsha Blue #0066B3.
import { createLightTheme, createDarkTheme } from '@fluentui/react-components';
import type { BrandVariants, Theme } from '@fluentui/react-components';

type Ramp = Record<number, string>;

export const S = {
  blue: { 50: '#F1F8FF', 100: '#E0EFFF', 200: '#C0DEFF', 300: '#90C6FF', 400: '#6AA9EB', 500: '#448DD5', 600: '#0066B3', 700: '#00599E', 800: '#00437A', 900: '#002C53', 950: '#001B37' },
  green: { 50: '#EAFCED', 100: '#D1F9D8', 200: '#9FF2B0', 300: '#6EDC8A', 400: '#3FC168', 500: '#00AB4E', 600: '#00853B', 700: '#006B2E', 800: '#005121', 900: '#003614' },
  red: { 50: '#FFF4F2', 100: '#FFE6E3', 200: '#FFCDC7', 300: '#FFA69C', 400: '#FF7268', 500: '#ED413E', 600: '#DB2128', 700: '#A80013', 800: '#81000C' },
  grey: { 50: '#F5F7F9', 100: '#EBEDEF', 200: '#D9DBDD', 300: '#BFC1C4', 400: '#A7A9AC', 500: '#87898C', 600: '#6D6F71', 700: '#57585A', 800: '#414244', 900: '#2B2B2C', 950: '#1A1B1B' },
};

const brand: BrandVariants = {
  10: S.blue[950], 20: S.blue[900], 30: '#00375F', 40: S.blue[800], 50: '#004E8C', 60: S.blue[700], 70: '#0060A9',
  80: '#0066B3', 90: '#2279C4', 100: S.blue[500], 110: '#579BE0', 120: S.blue[400], 130: '#7DB7F5',
  140: S.blue[300], 150: S.blue[200], 160: S.blue[100],
};

// Status hues remapped to logo colours: success → Swoosh Green, danger/warning → Swoosh Red.
const status = (k: string, c: Ramp) => ({
  [`colorStatus${k}Background1`]: c[50], [`colorStatus${k}Background2`]: c[200], [`colorStatus${k}Background3`]: c[600],
  [`colorStatus${k}Foreground1`]: c[700], [`colorStatus${k}Foreground2`]: c[700], [`colorStatus${k}Foreground3`]: c[600],
  [`colorStatus${k}ForegroundInverted`]: c[200],
  [`colorStatus${k}Border1`]: c[200], [`colorStatus${k}Border2`]: c[600], [`colorStatus${k}BorderActive`]: c[600],
});
const palette = (k: string, c: Ramp) => ({
  [`colorPalette${k}Background1`]: c[50], [`colorPalette${k}Background2`]: c[200], [`colorPalette${k}Background3`]: c[600],
  [`colorPalette${k}Foreground1`]: c[700], [`colorPalette${k}Foreground2`]: c[700], [`colorPalette${k}Foreground3`]: c[600],
  [`colorPalette${k}Border1`]: c[200], [`colorPalette${k}Border2`]: c[600], [`colorPalette${k}BorderActive`]: c[600],
});

const baseLight: Theme = {
  ...createLightTheme(brand),
  // Harsha type (HDS ds.css): Noto Sans for all body, labels, buttons; Eurostile Extended is applied in CSS to titles and big numbers.
  fontFamilyBase: "'Noto Sans','Noto Sans Gujarati','Noto Sans Devanagari',system-ui,sans-serif",
  fontFamilyMonospace: "'Cascadia Mono',Consolas,'SF Mono',Menlo,monospace",
  // Cool Engineers Grey neutrals; Stroke1 ≥ 3:1 so control and card edges stay visible.
  colorNeutralForeground1: '#231F20',
  colorNeutralForeground2: S.grey[800],
  colorNeutralForeground3: S.grey[700],
  colorNeutralForeground4: S.grey[600],
  colorNeutralBackground2: '#F0F3F7', // HDS bg
  colorNeutralBackground3: '#F0F2F4',
  colorNeutralBackground4: S.grey[100],
  colorNeutralStroke1: S.grey[500],
  colorNeutralStroke2: S.grey[200],
  colorNeutralStroke3: S.grey[100],
  colorNeutralStrokeAccessible: S.grey[700],
  ...status('Success', S.green), ...palette('Green', S.green),
  ...status('Danger', S.red), ...palette('Red', S.red),
  ...status('Warning', S.red), ...palette('DarkOrange', S.red), ...palette('Yellow', S.red),
};

// HDS dark column (palette.js SEM): cool Engineers Grey surfaces, light ink, soft status fills.
export const DARK = {
  bg: '#0C0D10', surface: '#15171A', surface2: '#202225', surface3: '#292B2E', line: '#333538', lineStrong: '#7E8084',
  text: '#F1F4F8', text2: '#C5C7CB', text3: '#A2A5A8', tintSoft: '#132A3D', tintLine: '#2A4F70',
  okSoft: '#0F2A1C', celebrateSoft: '#0D2814', washGrowth: '#0B2010', critSoft: '#431613', warnLine: '#662F2B',
};
const statusDark = (k: string, c: Ramp, soft: string, fg: string, line: string) => ({
  [`colorStatus${k}Background1`]: soft, [`colorStatus${k}Background2`]: soft, [`colorStatus${k}Background3`]: c[400],
  [`colorStatus${k}Foreground1`]: fg, [`colorStatus${k}Foreground2`]: fg, [`colorStatus${k}Foreground3`]: fg,
  [`colorStatus${k}ForegroundInverted`]: c[700],
  [`colorStatus${k}Border1`]: line, [`colorStatus${k}Border2`]: fg, [`colorStatus${k}BorderActive`]: fg,
});
const paletteDark = (k: string, soft: string, fg: string, line: string) => ({
  [`colorPalette${k}Background1`]: soft, [`colorPalette${k}Background2`]: soft, [`colorPalette${k}Background3`]: fg,
  [`colorPalette${k}Foreground1`]: fg, [`colorPalette${k}Foreground2`]: fg, [`colorPalette${k}Foreground3`]: fg,
  [`colorPalette${k}Border1`]: line, [`colorPalette${k}Border2`]: fg, [`colorPalette${k}BorderActive`]: fg,
});
const baseDark: Theme = {
  ...createDarkTheme(brand),
  fontFamilyBase: baseLight.fontFamilyBase,
  fontFamilyMonospace: baseLight.fontFamilyMonospace,
  colorNeutralForeground1: DARK.text,
  colorNeutralForeground2: DARK.text2,
  colorNeutralForeground3: DARK.text3,
  colorNeutralForeground4: DARK.lineStrong,
  colorNeutralBackground1: DARK.surface,
  colorNeutralBackground1Hover: DARK.surface2,
  colorNeutralBackground1Pressed: DARK.surface3,
  colorNeutralBackground1Selected: DARK.surface3,
  colorNeutralBackground2: DARK.bg,
  colorNeutralBackground3: DARK.bg,
  colorNeutralBackground4: DARK.surface2,
  colorSubtleBackgroundHover: DARK.surface2,
  colorSubtleBackgroundPressed: DARK.surface3,
  colorSubtleBackgroundSelected: DARK.surface3,
  colorNeutralStroke1: DARK.lineStrong,
  colorNeutralStroke2: DARK.line,
  colorNeutralStroke3: DARK.surface3,
  colorNeutralStrokeAccessible: DARK.text3,
  ...statusDark('Success', S.green, DARK.okSoft, S.green[300], S.green[800]), ...paletteDark('Green', DARK.okSoft, S.green[300], S.green[800]),
  ...statusDark('Danger', S.red, DARK.critSoft, S.red[300], DARK.warnLine), ...paletteDark('Red', DARK.critSoft, S.red[300], DARK.warnLine),
  ...statusDark('Warning', S.red, DARK.critSoft, S.red[300], DARK.warnLine), ...paletteDark('DarkOrange', DARK.critSoft, S.red[300], DARK.warnLine),
  ...paletteDark('Yellow', DARK.critSoft, S.red[300], DARK.warnLine),
};

// Chart colours: every series at least 3:1 against its card (white, or #15171A in dark), stepped by lightness so
// neighbours stay apart; white gaps separate stacked segments and donut slices. Green is kept for "done", so it is not a category.
export const viz = (dark: boolean): { series: string[]; parts: string[]; one: string } => (dark
  ? { series: [S.blue[300], S.blue[500], S.blue[600]], parts: [S.blue[300], S.blue[500], S.blue[600], S.grey[400], S.grey[500]], one: S.blue[300] }
  : { series: [S.blue[800], S.blue[600], S.blue[500]], parts: [S.blue[800], S.blue[600], S.blue[500], S.grey[600], S.grey[500]], one: S.blue[700] });

// App tokens: colours Fluent has no token for (washes, glass, brand gradients, sheet lines). They go into the theme object,
// so FluentProvider writes them as CSS variables (--appGlass …) for light and dark from the first render: one source of truth.
const BRAND_HUES = {
  appSkyDeep: S.blue[800], appSky: S.blue[600], appSkyLight: S.blue[400], appGrowthDeep: S.green[800], appGrowth: S.green[700], appGrowthLight: S.green[500],
  appSealDeep: '#171D6C', appSeal: '#303F99', appSealLight: '#AEBFF6', appPip: '#CF081C', appPaper: '#FFFFFF',
};
const LIGHT_APP = {
  ...BRAND_HUES,
  appBg: '#F0F3F7', appWash: 'rgba(192, 222, 255, .28)', appSkyWash: S.blue[50], appGrowthWash: S.green[50], appSurface: '#FFFFFF',
  appGlass: 'rgba(255, 255, 255, .42)', appGlassLine: 'rgba(255, 255, 255, .7)', appGlassStrong: 'rgba(255, 255, 255, .72)', appGlassStrongLine: 'rgba(255, 255, 255, .9)',
  appAcrylic: 'rgba(255, 255, 255, .78)', appShellSolid: '#F0F2F4', appHoverGlass: 'rgba(255, 255, 255, .72)', appChip: 'rgba(255, 255, 255, .85)',
  appHairline: 'rgba(35, 31, 32, .08)', appCardLine: 'rgba(0, 67, 122, .08)', appOk: S.green[700], appOkLine: 'rgba(0, 107, 46, .18)', appPdf: S.red[600],
  appAurora1: 'rgba(144, 198, 255, .6)', appAurora1Clear: 'rgba(144, 198, 255, 0)', appAurora2: 'rgba(192, 222, 255, .7)', appAurora2Clear: 'rgba(192, 222, 255, 0)',
  appAurora3: 'rgba(159, 242, 176, .55)', appAurora3Clear: 'rgba(159, 242, 176, 0)', appGrowthGlow: 'rgba(159, 242, 176, .55)', appGrowthGlowClear: 'rgba(159, 242, 176, 0)',
  appSheetLine: S.grey[200], appSheetHead: S.grey[50],
};
const DARK_APP: typeof LIGHT_APP = {
  ...BRAND_HUES,
  appBg: DARK.bg, appWash: 'rgba(19, 42, 61, .9)', appSkyWash: DARK.tintSoft, appGrowthWash: DARK.washGrowth, appSurface: DARK.surface,
  appGlass: 'rgba(21, 23, 26, .55)', appGlassLine: 'rgba(51, 53, 56, .9)', appGlassStrong: 'rgba(32, 34, 37, .78)', appGlassStrongLine: 'rgba(51, 53, 56, .9)',
  appAcrylic: 'rgba(32, 34, 37, .82)', appShellSolid: DARK.surface, appHoverGlass: 'rgba(41, 43, 46, .9)', appChip: DARK.surface3,
  appHairline: 'rgba(241, 244, 248, .08)', appCardLine: DARK.line, appOk: S.green[300], appOkLine: 'rgba(110, 220, 138, .25)', appPdf: S.red[400],
  appAurora1: 'rgba(0, 89, 158, .5)', appAurora1Clear: 'rgba(0, 89, 158, 0)', appAurora2: 'rgba(0, 67, 122, .55)', appAurora2Clear: 'rgba(0, 67, 122, 0)',
  appAurora3: 'rgba(0, 81, 33, .5)', appAurora3Clear: 'rgba(0, 81, 33, 0)', appGrowthGlow: 'rgba(0, 81, 33, .5)', appGrowthGlowClear: 'rgba(0, 81, 33, 0)',
  appSheetLine: DARK.line, appSheetHead: DARK.surface2,
};
/** Fluent theme plus the app's own tokens; FluentProvider writes every key as a CSS variable. */
export type AppTheme = Theme & typeof LIGHT_APP;
export const theme: AppTheme = { ...baseLight, ...LIGHT_APP };
export const darkTheme: AppTheme = { ...baseDark, ...DARK_APP };

