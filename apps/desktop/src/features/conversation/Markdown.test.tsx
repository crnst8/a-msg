import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppContext } from '@/state/queries'
import { Markdown } from './Markdown'

const openExternal = vi.fn()
afterEach(cleanup)
const show = (text: string) =>
  render(
    <AppContext.Provider value={{ transport: { openExternal } as never, sync: {} as never }}>
      <Markdown text={text} />
    </AppContext.Provider>,
  )

describe('Markdown', () => {
  it('renders the supported structure', () => {
    const { container } = show('# One\n\n## Two\n\n**b** *i*\n\n- item\n\n> quote\n\n| a |\n|---|\n| 1 |\n\n```py\nx = 1\n```')
    for (const selector of ['h1', 'h2', 'strong', 'em', 'li', 'blockquote', 'table', 'pre']) {
      expect(container.querySelector(selector)).not.toBeNull()
    }
  })
  it('blocks raw HTML', () => {
    const { container } = show('<script>alert(1)</script><img src=x onerror=alert(1)>\n\nsafe')
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain('safe')
  })
  it('removes dangerous link schemes', () => {
    const { container } = show('[a](javascript:alert(1)) [b](data:text/html,x) [c](file:///etc/passwd)')
    for (const link of container.querySelectorAll('a')) expect(link.getAttribute('href')).toBeNull()
    expect(container.textContent).toContain('a')
  })
  it('opens links through the native handler', () => {
    show('[site](https://example.com)')
    screen.getByRole('link').click()
    expect(openExternal).toHaveBeenCalledWith('https://example.com')
  })
  it('does not load remote images', () => {
    const { container } = show('![pixel](https://tracker.example/p.gif)')
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByRole('link').textContent).toBe('pixel')
  })
  it('keeps malformed Markdown readable', () => {
    const { container } = show('**unclosed [link](http://x\n\n```\nno end')
    expect(container.textContent).toContain('unclosed')
    expect(container.textContent).toContain('no end')
  })
})
