// Harsha Fluent theme. Ramps are the exact HDS.PALETTE OKLCH scales
// (HarshaLMS_V2/tools/design/hds/src/palette.js); slot 80 = Harsha Blue #0066B3.
import { createLightTheme } from '@fluentui/react-components';

export const S = {
  blue: { 50: '#F1F8FF', 100: '#E0EFFF', 200: '#C0DEFF', 300: '#90C6FF', 400: '#6AA9EB', 500: '#448DD5', 600: '#0066B3', 700: '#00599E', 800: '#00437A', 900: '#002C53', 950: '#001B37' },
  green: { 50: '#EAFCED', 100: '#D1F9D8', 200: '#9FF2B0', 500: '#00AB4E', 600: '#00853B', 700: '#006B2E', 800: '#005121' },
  red: { 50: '#FFF4F2', 100: '#FFE6E3', 200: '#FFCDC7', 600: '#DB2128', 700: '#A80013' },
  grey: { 50: '#F5F7F9', 100: '#EBEDEF', 200: '#D9DBDD', 300: '#BFC1C4', 400: '#A7A9AC', 500: '#87898C', 600: '#6D6F71', 700: '#57585A', 800: '#414244', 900: '#2B2B2C' },
};

const brand = {
  10: S.blue[950], 20: S.blue[900], 30: '#00375F', 40: S.blue[800], 50: '#004E8C', 60: S.blue[700], 70: '#0060A9',
  80: '#0066B3', 90: '#2279C4', 100: S.blue[500], 110: '#579BE0', 120: S.blue[400], 130: '#7DB7F5',
  140: S.blue[300], 150: S.blue[200], 160: S.blue[100],
};

// Status hues remapped to logo colours: success → Swoosh Green, danger/warning → Swoosh Red.
const status = (k, c) => ({
  [`colorStatus${k}Background1`]: c[50], [`colorStatus${k}Background2`]: c[200], [`colorStatus${k}Background3`]: c[600],
  [`colorStatus${k}Foreground1`]: c[700], [`colorStatus${k}Foreground2`]: c[700], [`colorStatus${k}Foreground3`]: c[600],
  [`colorStatus${k}ForegroundInverted`]: c[200],
  [`colorStatus${k}Border1`]: c[200], [`colorStatus${k}Border2`]: c[600], [`colorStatus${k}BorderActive`]: c[600],
});
const palette = (k, c) => ({
  [`colorPalette${k}Background1`]: c[50], [`colorPalette${k}Background2`]: c[200], [`colorPalette${k}Background3`]: c[600],
  [`colorPalette${k}Foreground1`]: c[700], [`colorPalette${k}Foreground2`]: c[700], [`colorPalette${k}Foreground3`]: c[600],
  [`colorPalette${k}Border1`]: c[200], [`colorPalette${k}Border2`]: c[600], [`colorPalette${k}BorderActive`]: c[600],
});

export const theme = {
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
