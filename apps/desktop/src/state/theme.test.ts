import { describe, expect, it, vi } from 'vitest'

vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {} }))
const { PALETTES, contrast, exportTheme, parseTheme, suggestReadable } = await import('./theme')

const valid = (extra: object = {}) => JSON.stringify({ format: 'amsg-theme', version: 1, palette: 'paper', ...extra })

describe('theme files', () => {
  it('round trips an export', () => {
    expect(parseTheme(exportTheme()).palette).toBe('paper')
  })
  it('refuses another format and unknown versions', () => {
    expect(() => parseTheme('{}')).toThrow('not an a-msg theme')
    expect(() => parseTheme(valid({ version: 2 }))).toThrow('not supported')
    expect(() => parseTheme('not json')).toThrow('not valid JSON')
  })
  it('never accepts arbitrary CSS', () => {
    expect(() => parseTheme(valid({ light: { canvas: 'url(https://x)' } }))).toThrow('#RRGGBB')
    expect(() => parseTheme(valid({ light: { canvas: '#000;}body{display:none' } }))).toThrow('#RRGGBB')
    expect(() => parseTheme(valid({ light: { '--anything': '#000000' } }))).toThrow('Unknown token')
    expect(() => parseTheme(valid({ css: 'body{}' }))).toThrow('Unknown field')
  })
  it('bounds fonts and sizes', () => {
    expect(() => parseTheme(valid({ font: 'Comic Sans' }))).toThrow('Unknown font')
    expect(() => parseTheme(valid({ textSize: 40 }))).toThrow('between 14 and 20')
  })
})

describe('approved tokens', () => {
  it('keeps the approved user surface opaque and exact', () => {
    expect(PALETTES.paper.light.messageUser).toBe('#D5D0B6')
  })
})

describe('contrast', () => {
  const pairs = [
    ['text', 'canvas'],
    ['text', 'surface'],
    ['textSecondary', 'canvas'],
    ['textSecondary', 'surface'],
    ['messageUserText', 'messageUser'],
    ['onAccent', 'accent'],
  ] as const
  for (const palette of ['paper', 'sage'] as const) {
    for (const scheme of ['light', 'dark'] as const) {
      it(`${palette} ${scheme} text meets 4.5:1`, () => {
        const tokens = PALETTES[palette][scheme]
        for (const [fg, bg] of pairs) expect(contrast(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(4.5)
      })
    }
  }
  it('rail icons meet 3:1 on the rail', () => {
    expect(contrast('#959593', '#1E1E1E')).toBeGreaterThanOrEqual(3)
  })
  it('selected rail state meets 3:1', () => {
    expect(contrast('#E3DECC', '#3B3C35')).toBeGreaterThanOrEqual(3)
  })
  it('focus rings meet 3:1 on their surfaces', () => {
    expect(contrast('#7B7135', '#E3DECC')).toBeGreaterThanOrEqual(3)
    expect(contrast('#7B7135', '#FFFFFF')).toBeGreaterThanOrEqual(3)
    expect(contrast('#D8D39F', '#24251F')).toBeGreaterThanOrEqual(3)
    expect(contrast('#D8D39F', '#30312B')).toBeGreaterThanOrEqual(3)
  })
  it('suggests a readable replacement', () => {
    const suggestion = suggestReadable('#CCCCCC', '#FFFFFF')
    expect(contrast(suggestion, '#FFFFFF')).toBeGreaterThanOrEqual(4.5)
  })
})
