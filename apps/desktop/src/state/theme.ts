import { DEFAULTS, preferences, updatePreferences } from './preferences'
import type { Preferences } from './preferences'

export interface ThemeTokens {
  canvas: string
  surface: string
  text: string
  textSecondary: string
  messageUser: string
  messageUserText: string
  rail: string
  railIcon: string
  accent: string
  onAccent: string
}

export type PaletteName = 'paper' | 'sage'
export type Scheme = 'light' | 'dark'

export const TOKEN_KEYS: (keyof ThemeTokens)[] = [
  'canvas',
  'surface',
  'text',
  'textSecondary',
  'messageUser',
  'messageUserText',
  'accent',
  'onAccent',
  'rail',
  'railIcon',
]

const RAIL = { rail: '#1E1E1E', railIcon: '#959593' }

export const PALETTES: Record<PaletteName, Record<Scheme, ThemeTokens>> = {
  paper: {
    light: {
      ...RAIL,
      canvas: '#E3DECC',
      surface: '#FFFFFF',
      text: '#24251F',
      textSecondary: '#5C5D51',
      messageUser: '#D5D0B6',
      messageUserText: '#24251F',
      accent: '#31321B',
      onAccent: '#F7F4E9',
    },
    dark: {
      ...RAIL,
      canvas: '#24251F',
      surface: '#30312B',
      text: '#E3DECC',
      textSecondary: '#BBB9A9',
      messageUser: '#515239',
      messageUserText: '#F4F0DF',
      accent: '#D8D39F',
      onAccent: '#24251F',
    },
  },
  sage: {
    light: {
      ...RAIL,
      canvas: '#DEDED6',
      surface: '#FAFAF6',
      text: '#252823',
      textSecondary: '#565D53',
      messageUser: '#B9C1AF',
      messageUserText: '#20251C',
      accent: '#394B32',
      onAccent: '#F1F4E9',
    },
    dark: {
      ...RAIL,
      canvas: '#232823',
      surface: '#2E352F',
      text: '#E1E5D9',
      textSecondary: '#B8C1B2',
      messageUser: '#475540',
      messageUserText: '#F1F4E9',
      accent: '#BDCAA9',
      onAccent: '#232823',
    },
  },
}

const STATES: Record<Scheme, Record<string, string>> = {
  light: {
    '--success': '#34513A',
    '--success-surface': '#DFE7D8',
    '--warning': '#694619',
    '--warning-surface': '#F2E3BF',
    '--error': '#853F36',
    '--error-surface': '#F2DED6',
    '--find': '#E7D27A',
    '--on-find': '#24251F',
  },
  dark: {
    '--success': '#C1D4BA',
    '--success-surface': '#293B2D',
    '--warning': '#EDD29A',
    '--warning-surface': '#44351F',
    '--error': '#F1BCB0',
    '--error-surface': '#472F2A',
    '--find': '#8A7A2E',
    '--on-find': '#F4F0DF',
  },
}

const FONTS: Record<Preferences['font'], string> = {
  haas: "'Helvetica Neue', Helvetica, Arial, sans-serif",
  system: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
  serif: "'Iowan Old Style', Charter, Georgia, serif",
}

const media = window.matchMedia('(prefers-color-scheme: dark)')

export function activeScheme(prefs: Preferences = preferences()): Scheme {
  if (prefs.mode === 'system') return media.matches ? 'dark' : 'light'
  return prefs.mode
}

export function resolveTokens(prefs: Preferences = preferences()): ThemeTokens {
  const scheme = activeScheme(prefs)
  return { ...PALETTES[prefs.palette][scheme], ...(prefs.custom[scheme] ?? {}) }
}

function hex(value: string): [number, number, number] {
  const n = Number.parseInt(value.slice(1), 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}
function toHex(rgb: number[]): string {
  return `#${rgb.map((c) => Math.round(Math.min(255, Math.max(0, c))).toString(16).padStart(2, '0')).join('')}`.toUpperCase()
}
function mix(a: string, b: string, amount: number): string {
  const [x, y] = [hex(a), hex(b)]
  return toHex(x.map((c, i) => c + (y[i] - c) * amount))
}
function luminance(value: string): number {
  const [r, g, b] = hex(value).map((c) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

export function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}

/** Moves a color toward black or white until it reaches the target contrast against its background. */
export function suggestReadable(color: string, background: string, target = 4.5): string {
  const toward = luminance(background) > 0.4 ? '#000000' : '#FFFFFF'
  for (let step = 1; step <= 20; step++) {
    const candidate = mix(color, toward, step / 20)
    if (contrast(candidate, background) >= target) return candidate
  }
  return toward
}

export function applyTheme(prefs: Preferences = preferences()): void {
  const scheme = activeScheme(prefs)
  const t = resolveTokens(prefs)
  const root = document.documentElement
  const set = (name: string, value: string) => root.style.setProperty(name, value)
  set('--canvas', t.canvas)
  set('--surface', t.surface)
  set('--text', t.text)
  set('--text-2', t.textSecondary)
  set('--user', t.messageUser)
  set('--user-text', t.messageUserText)
  set('--rail', t.rail)
  set('--rail-icon', t.railIcon)
  set('--accent', t.accent)
  set('--on-accent', t.onAccent)
  set('--rail-active', '#3B3C35')
  set('--rail-text', '#BBB9A9')
  set('--rail-bright', '#E3DECC')
  set('--rail-new', '#31321B')
  set('--rail-new-mark', '#D8D39F')
  set('--brand', scheme === 'light' ? '#7B7135' : '#B8AD62')
  // Derived roles stay relationships between tokens, so a custom palette keeps them consistent.
  set('--line', `color-mix(in srgb, ${t.text} 13%, transparent)`)
  set('--line-strong', `color-mix(in srgb, ${t.text} 28%, transparent)`)
  set('--hover', `color-mix(in srgb, ${t.text} 7%, transparent)`)
  set('--press', `color-mix(in srgb, ${t.text} 12%, transparent)`)
  set('--well', scheme === 'light' ? mix(t.canvas, t.surface, 0.55) : mix(t.canvas, t.surface, 0.5))
  set('--code-bg', scheme === 'light' ? mix(t.canvas, t.surface, 0.6) : mix(t.surface, '#000000', 0.18))
  set('--code-keyword', scheme === 'light' ? '#5C5A1E' : '#D8D39F')
  set('--code-string', scheme === 'light' ? '#34513A' : '#C1D4BA')
  set('--code-number', scheme === 'light' ? '#853F36' : '#F1BCB0')
  set('--focus', scheme === 'light' ? '#7B7135' : '#D8D39F')
  set('--selection', `color-mix(in srgb, ${t.accent} 24%, transparent)`)
  set('--scroll', `color-mix(in srgb, ${t.text} 24%, transparent)`)
  set('--scrim', scheme === 'light' ? 'rgba(36,37,31,.34)' : 'rgba(0,0,0,.5)')
  for (const [name, value] of Object.entries(STATES[scheme])) set(name, value)
  set('--font-ui', FONTS[prefs.font])
  root.dataset.scheme = scheme
  root.dataset.density = prefs.density
  root.style.colorScheme = scheme
  root.style.fontSize = `${prefs.textSize}px`
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t.rail)
}

media.addEventListener('change', () => applyTheme())

export const THEME_FORMAT = 'amsg-theme'
export const THEME_VERSION = 1

export interface ThemeFile {
  format: typeof THEME_FORMAT
  version: typeof THEME_VERSION
  palette: PaletteName
  light?: Partial<ThemeTokens>
  dark?: Partial<ThemeTokens>
  font?: Preferences['font']
  textSize?: number
  density?: Preferences['density']
}

export function exportTheme(): string {
  const p = preferences()
  const file: ThemeFile = {
    format: THEME_FORMAT,
    version: THEME_VERSION,
    palette: p.palette,
    light: p.custom.light,
    dark: p.custom.dark,
    font: p.font,
    textSize: p.textSize,
    density: p.density,
  }
  return JSON.stringify(file, null, 2)
}

const COLOR = /^#[0-9a-fA-F]{6}$/

function checkTokens(value: unknown, where: string): Partial<ThemeTokens> | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'object' || value === null) throw new Error(`${where} must be an object`)
  const out: Partial<ThemeTokens> = {}
  for (const [key, color] of Object.entries(value)) {
    if (!TOKEN_KEYS.includes(key as keyof ThemeTokens)) throw new Error(`Unknown token: ${key}`)
    if (typeof color !== 'string' || !COLOR.test(color)) throw new Error(`${key} must be a #RRGGBB color`)
    out[key as keyof ThemeTokens] = color.toUpperCase()
  }
  return out
}

/** Accepts only known keys, #RRGGBB colors, listed fonts and bounded sizes. Never arbitrary CSS. */
export function parseTheme(text: string): ThemeFile {
  let data: any
  try {
    data = JSON.parse(text)
  } catch {
    throw new Error('That file is not valid JSON.')
  }
  if (data?.format !== THEME_FORMAT) throw new Error('That file is not an a-msg theme.')
  if (data.version !== THEME_VERSION) throw new Error(`Theme version ${data.version} is not supported.`)
  const allowed = new Set(['format', 'version', 'palette', 'light', 'dark', 'font', 'textSize', 'density'])
  for (const key of Object.keys(data)) if (!allowed.has(key)) throw new Error(`Unknown field: ${key}`)
  if (!(data.palette in PALETTES)) throw new Error('Unknown palette.')
  if (data.font !== undefined && !(data.font in FONTS)) throw new Error('Unknown font.')
  if (data.textSize !== undefined && !(Number.isFinite(data.textSize) && data.textSize >= 14 && data.textSize <= 20)) {
    throw new Error('Text size must be between 14 and 20.')
  }
  if (data.density !== undefined && !['comfortable', 'compact'].includes(data.density)) throw new Error('Unknown density.')
  return { ...data, light: checkTokens(data.light, 'light'), dark: checkTokens(data.dark, 'dark') }
}

export function importTheme(text: string): void {
  const file = parseTheme(text)
  updatePreferences({
    palette: file.palette,
    custom: { light: file.light, dark: file.dark },
    font: file.font ?? DEFAULTS.font,
    textSize: file.textSize ?? DEFAULTS.textSize,
    density: file.density ?? DEFAULTS.density,
  })
}

export function resetTheme(): void {
  const { palette, custom, font, textSize, density, mode } = DEFAULTS
  updatePreferences({ palette, custom, font, textSize, density, mode })
}
