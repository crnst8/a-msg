import { expect, test } from '@playwright/test'
import { article, composer, newSpace, noPageOverflow, open, scroller, send, unique } from './helpers'

const EVIDENCE = process.env.AMSG_EVIDENCE

test.describe('Sizes and themes', () => {
  for (const [width, height] of [
    [640, 480],
    [1024, 768],
    [1440, 900],
  ]) {
    for (const scheme of ['light', 'dark'] as const) {
      test(`${width}x${height} ${scheme} holds its layout`, async ({ page }) => {
        await page.emulateMedia({ colorScheme: scheme })
        await page.setViewportSize({ width, height })
        await open(page)
        await noPageOverflow(page)
        if (EVIDENCE) await page.screenshot({ path: `${EVIDENCE}/general-${width}x${height}-${scheme}.png` })
      })
    }
  }

  for (const rail of ['left', 'right', 'top', 'bottom']) {
    for (const [width, height] of [
      [640, 480],
      [1024, 768],
    ]) {
      test(`rail ${rail} works at ${width}x${height}`, async ({ page }) => {
        await page.setViewportSize({ width, height })
        await open(page)
        await page.evaluate((value) => localStorage.setItem('amsg.preferences', JSON.stringify({ version: 1, rail: value })), rail)
        await page.reload()
        await page.waitForSelector('nav')
        await noPageOverflow(page)
        const settings = page.getByRole('button', { name: /^Settings/ })
        await expect(settings).toBeInViewport({ ratio: 1 })
        const input = await composer(page).boundingBox()
        const nav = await page.locator('nav').boundingBox()
        expect(input).not.toBeNull()
        if (rail === 'bottom') expect(input!.y + input!.height).toBeLessThanOrEqual(nav!.y)
        await settings.click()
        await expect(page.getByRole('heading', { name: 'Settings' })).toBeVisible()
        await page.evaluate(() => localStorage.removeItem('amsg.preferences'))
      })
    }
  }
})

test('navigation uses the supplied icons, at the specified size, in 40 px targets or larger', async ({ page }) => {
  await open(page)
  for (const [label, file] of [
    ['General', 'general.png'],
    ['Library', 'library.png'],
    ['Code Connector', 'code.png'],
    ['Settings', 'settings.png'],
  ]) {
    const button = page.getByRole('button', { name: new RegExp(`^${label}`) })
    const image = button.locator('img')
    await expect(image).toHaveAttribute('src', new RegExp(`/assets/navigation/${file}$`))
    const size = await image.boundingBox()
    expect(size!.width).toBeGreaterThanOrEqual(24)
    expect(size!.width).toBeLessThanOrEqual(28)
    expect(await image.evaluate((node) => getComputedStyle(node).objectFit)).toBe('contain')
    const target = await button.boundingBox()
    expect(target!.width).toBeGreaterThanOrEqual(40)
    expect(target!.height).toBeGreaterThanOrEqual(40)
  }
})

test('the approved surfaces render exactly', async ({ page }) => {
  await open(page)
  await expect(article(page).first().locator('div').first()).toBeVisible()
  const card = await page.locator('article[data-role="user"] > div').first()
  expect(await card.evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(213, 208, 182)')
  expect(await card.evaluate((node) => getComputedStyle(node).opacity)).toBe('1')
  expect(await page.locator('nav').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(30, 30, 30)')
  expect(await page.locator('main').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(227, 222, 204)')
})

test('selection is not communicated by hue alone', async ({ page }) => {
  await open(page)
  await expect(page.getByRole('button', { name: /^General/ })).toHaveAttribute('aria-current', 'page')
  const tile = await page.getByRole('button', { name: /^General/ }).evaluate((node) => getComputedStyle(node, '::before').borderTopWidth)
  expect(tile).toBe('1px')
})

test('wide tables and code scroll inside the card, never the page', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: /^Server maintenance/ }).click()
  await page.setViewportSize({ width: 640, height: 480 })
  await expect(page.locator('table')).toBeVisible()
  await noPageOverflow(page)
  const table = await page.locator('table').evaluate((node) => {
    const box = node.parentElement!
    return { scrolls: box.scrollWidth > box.clientWidth, overflow: getComputedStyle(box).overflowX }
  })
  expect(table).toEqual({ scrolls: true, overflow: 'auto' })
  const firstCell = await page.locator('td').first().evaluate((node) => node.getBoundingClientRect().height)
  expect(firstCell).toBeLessThan(60)
})

test('the composer grows to six lines, then scrolls', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Composer'))
  const input = composer(page)
  const one = (await input.boundingBox())!.height
  await input.fill('1\n2\n3\n4\n5\n6')
  const six = (await input.boundingBox())!.height
  await input.fill('1\n2\n3\n4\n5\n6\n7\n8\n9\n10')
  const ten = (await input.boundingBox())!.height
  expect(six).toBeGreaterThan(one * 4)
  expect(Math.abs(ten - six)).toBeLessThanOrEqual(10)
  expect(await input.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true)
})

test('Enter sends, Shift+Enter breaks the line, composition never sends', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Keys'))
  const input = composer(page)
  await input.click()
  await page.keyboard.type('line one')
  await page.keyboard.press('Shift+Enter')
  await page.keyboard.type('line two')
  await expect(input).toHaveValue('line one\nline two')
  await input.evaluate((node) => {
    node.dispatchEvent(new CompositionEvent('compositionstart'))
    node.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, isComposing: true, keyCode: 229 }))
  })
  await expect(input).toHaveValue('line one\nline two')
  await input.evaluate((node) => node.dispatchEvent(new CompositionEvent('compositionend')))
  await page.keyboard.press('Enter')
  await expect(input).toHaveValue('')
  await expect(article(page).first()).toContainText('line one')
})

test('Send is disabled for empty text and a draft survives leaving the space', async ({ page }) => {
  await open(page)
  const title = unique('Draft')
  await newSpace(page, title)
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled()
  await composer(page).fill('unfinished thought')
  await page.getByRole('button', { name: /^General/ }).click()
  await expect(composer(page)).toHaveValue('')
  await page.getByRole('button', { name: new RegExp(`^${title}`) }).click()
  await expect(composer(page)).toHaveValue('unfinished thought')
  await composer(page).fill('')
})

test('sending shows a card at once and keeps one message through acceptance', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Send'))
  const started = Date.now()
  await send(page, 'quick message')
  await expect(page.locator('article[data-role="user"]')).toHaveCount(1)
  expect(Date.now() - started).toBeLessThan(1000)
  await expect(page.locator('article[data-role="agent"]')).toContainText('Received.')
  await expect(page.locator('article[data-role="user"]')).toHaveCount(1)
})

test('reload during a run ends with exactly one final message', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Reload'))
  await send(page, 'health check')
  await expect(page.locator('article[data-role="agent"]')).toBeVisible()
  await page.reload()
  await page.waitForSelector('nav')
  await expect(page.locator('article[data-role="agent"]').getByText('Job ID for reference')).toBeVisible({ timeout: 15000 })
  await expect(page.locator('article[data-role="user"]')).toHaveCount(1)
  await expect(page.locator('article[data-role="agent"]')).toHaveCount(1)
})

test('Stop asks, then reports the confirmed outcome with Retry', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Stop'))
  await send(page, 'slow job')
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  const reply = page.locator('article[data-role="agent"]')
  await expect(reply.getByText('Stopped')).toBeVisible()
  await expect(reply.getByRole('button', { name: 'Retry', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeVisible()
})

test('a failed run keeps partial text, and Retry is a labelled attempt', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Fail'))
  await send(page, 'please fail')
  const reply = page.locator('article[data-role="agent"]').first()
  await expect(reply.getByText('Starting the check')).toBeVisible()
  await expect(reply.getByText('lost its connection')).toBeVisible()
  await reply.getByRole('button', { name: 'Retry', exact: true }).click()
  await expect(page.locator('article[data-role="agent"]').nth(1).getByText('Attempt 2')).toBeVisible()
})

test('a message sent during a run waits visibly and can be edited or removed', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Queue'))
  await send(page, 'slow job')
  await expect(page.locator('article[data-role="agent"]')).toBeVisible()
  await send(page, 'queued text')
  const queued = page.locator('article[data-role="user"]').nth(1)
  await expect(queued.getByText('Queued', { exact: true })).toBeVisible()
  await queued.getByRole('button', { name: 'Edit', exact: true }).click()
  await page.getByLabel('Edit queued message').fill('edited text')
  await page.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(queued).toContainText('edited text')
  await queued.getByRole('button', { name: 'Remove', exact: true }).click()
  await expect(page.locator('article[data-role="user"]')).toHaveCount(1)
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.locator('article[data-role="agent"]').getByText('Stopped')).toBeVisible()
})

test('reading older output is not moved by streaming, and Jump to latest returns', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 560 })
  await open(page)
  await newSpace(page, unique('Follow'))
  await send(page, 'health report')
  await expect(page.getByRole('button', { name: 'Save vps-health-2026-11-05.md' })).toBeVisible({ timeout: 15000 })
  await send(page, 'slow job')
  await expect(page.locator('article[data-role="agent"]').nth(1).getByText('Health sweep')).toBeVisible({ timeout: 8000 })
  await scroller(page).evaluate((node) => (node.scrollTop = 40))
  await expect(page.getByRole('button', { name: 'Jump to latest' })).toBeVisible()
  const before = await scroller(page).evaluate((node) => ({ top: node.scrollTop, height: node.scrollHeight }))
  await page.waitForTimeout(900)
  const after = await scroller(page).evaluate((node) => ({ top: node.scrollTop, height: node.scrollHeight }))
  expect(after.top).toBe(before.top)
  expect(after.height).toBeGreaterThan(before.height)
  await page.getByRole('button', { name: 'Jump to latest' }).click()
  await page.waitForTimeout(300)
  const distance = await scroller(page).evaluate((node) => node.scrollHeight - node.clientHeight - node.scrollTop)
  expect(distance).toBeLessThanOrEqual(80)
  await page.getByRole('button', { name: 'Stop', exact: true }).click()
  await expect(page.locator('article[data-role="agent"]').nth(1).getByText('Stopped')).toBeVisible()
})

test('keyboard reaches navigation, menus and the composer', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: /^General/ }).focus()
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: /^Library/ })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Library' })).toBeVisible()
  await page.getByRole('button', { name: /^Server maintenance/ }).focus()
  await page.keyboard.press('Shift+F10')
  await expect(page.getByRole('menu')).toBeVisible()
  await expect(page.getByRole('menuitem', { name: 'Rename' })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await expect(page.getByRole('menuitem', { name: 'Change icon' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('menu')).toBeHidden()
  await expect(page.getByRole('button', { name: /^Server maintenance/ })).toBeFocused()
})

test('focus is visible on rail, controls and fields', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: /^General/ }).focus()
  await page.keyboard.press('Tab')
  const outline = await page.getByRole('button', { name: /^Library/ }).evaluate((node) => getComputedStyle(node).outlineStyle)
  expect(outline).toBe('solid')
})

test('command palette finds spaces, actions and message text', async ({ page }) => {
  await open(page)
  await page.keyboard.press('Control+k')
  const search = page.getByRole('combobox')
  await expect(search).toBeFocused()
  await search.fill('holiday')
  await expect(page.getByRole('option', { name: /Holiday planning/ }).first()).toBeVisible()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { name: 'Holiday planning' })).toBeVisible()
  await page.keyboard.press('Control+k')
  await page.getByRole('combobox').fill('zzzznothing')
  await expect(page.getByText('No results.')).toBeVisible()
  await page.keyboard.press('Escape')
})

test('find in conversation highlights and steps through matches', async ({ page }) => {
  await open(page)
  await page.keyboard.press('Control+f')
  const find = page.getByLabel('Find in conversation')
  await expect(find).toBeFocused()
  await find.fill('docker')
  await expect(page.getByText(/^\d+ of \d+$/)).toBeVisible()
  const painted = await page.evaluate(() => (CSS as any).highlights.get('find').size)
  expect(painted).toBeGreaterThan(0)
  await page.keyboard.press('Escape')
  await expect(find).toBeHidden()
  expect(await page.evaluate(() => (CSS as any).highlights.has('find'))).toBe(false)
})

test('settings apply immediately and reset', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: /^Settings/ }).click()
  await page.getByRole('radio', { name: 'Dark' }).click()
  expect(await page.evaluate(() => document.documentElement.dataset.scheme)).toBe('dark')
  expect(await page.locator('main').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(36, 37, 31)')
  await page.getByRole('radio', { name: 'Sage' }).click()
  expect(await page.locator('main').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(35, 40, 35)')
  await page.getByRole('radio', { name: 'Compact' }).click()
  await page.getByRole('radio', { name: 'Bottom' }).click()
  await expect(page.locator('nav')).toHaveAttribute('data-position', 'bottom')
  await page.getByRole('radio', { name: 'Left' }).click()
  await page.getByRole('button', { name: 'Reset' }).click()
  await page.getByRole('radio', { name: 'Light' }).click()
  expect(await page.locator('main').evaluate((node) => getComputedStyle(node).backgroundColor)).toBe('rgb(227, 222, 204)')
  await page.getByRole('radiogroup', { name: 'Mode' }).getByRole('radio', { name: 'System' }).click()
})

test('reduced motion removes transitions', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await open(page)
  const fast = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--fast').trim())
  expect(fast).toBe('0ms')
})

test('rail reorder has a keyboard path and an undo', async ({ page }) => {
  await open(page)
  const labels = () => page.locator('[role="list"] button').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('aria-label')))
  const before = await labels()
  await page.getByRole('button', { name: new RegExp(`^${before[0]!.split(',')[0]}`) }).focus()
  await page.keyboard.press('Shift+F10')
  await page.getByRole('menuitem', { name: 'Move down' }).click()
  await expect.poll(async () => (await labels())[1]?.split(',')[0]).toBe(before[0]!.split(',')[0])
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await labels())[0]?.split(',')[0]).toBe(before[0]!.split(',')[0])
})

test('offline keeps the message queued, and reconnecting delivers it once', async ({ page, context }) => {
  await open(page)
  await newSpace(page, unique('Offline'))
  await page.route('**/api/**', (route) => route.abort())
  await context.setOffline(true)
  await send(page, 'written while offline')
  const card = page.locator('article[data-role="user"]')
  await expect(card.getByText('Queued', { exact: true })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: /Offline|Reconnecting/ }).first()).toBeVisible({ timeout: 8000 })
  await context.setOffline(false)
  await page.unroute('**/api/**')
  await expect(page.locator('article[data-role="agent"]')).toContainText('Received.', { timeout: 20000 })
  await expect(card).toHaveCount(1)
})

test('budget: optimistic send feedback stays under 100 ms', async ({ page }) => {
  test.setTimeout(60000)
  await open(page)
  await newSpace(page, unique('Budget'))
  const measure = (text: string) =>
    page.evaluate(
      (body) =>
        new Promise<number>((resolve) => {
          const box = document.querySelector('textarea')!
          Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(box, body)
          box.dispatchEvent(new Event('input', { bubbles: true }))
          setTimeout(() => {
            const before = document.querySelectorAll('article[data-role="user"]').length
            const started = performance.now()
            const observer = new MutationObserver(() => {
              if (document.querySelectorAll('article[data-role="user"]').length > before) {
                observer.disconnect()
                resolve(performance.now() - started)
              }
            })
            observer.observe(document.body, { childList: true, subtree: true })
            document.querySelector('form')!.requestSubmit()
          }, 50)
        }),
      text,
    )
  const samples: number[] = []
  for (let index = 0; index < 9; index++) {
    samples.push(await measure(`note ${index}`))
    await page.waitForTimeout(700)
  }
  const sorted = [...samples].sort((a, b) => a - b)
  const median = sorted[Math.floor(sorted.length / 2)]
  console.log(`optimistic send feedback: median ${median.toFixed(0)} ms, worst ${sorted.at(-1)!.toFixed(0)} ms`)
  expect(median).toBeLessThan(100)
})

test('fixed chrome does not move through send, streaming and completion', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Stable'))
  const chrome = () =>
    page.evaluate(() => {
      const box = (node: Element | null) => {
        const rect = node!.getBoundingClientRect()
        return [Math.round(rect.top), Math.round(rect.left), Math.round(rect.width)].join('/')
      }
      return {
        rail: box(document.querySelector('nav')),
        title: box(document.querySelector('header h1')),
        send: box(document.querySelector('form button[type="submit"], form button[aria-label="Stop"]')),
        composer: box(document.querySelector('form')),
      }
    })
  const before = await chrome()
  await send(page, 'health')
  const seen = new Set([JSON.stringify(before)])
  for (let sample = 0; sample < 12; sample++) {
    seen.add(JSON.stringify(await chrome()))
    await page.waitForTimeout(120)
  }
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeVisible({ timeout: 15000 })
  seen.add(JSON.stringify(await chrome()))
  expect([...seen]).toEqual([JSON.stringify(before)])
})

test('prepending an older page keeps the reader on the same message', async ({ page, request }) => {
  test.setTimeout(120000)
  await page.setViewportSize({ width: 900, height: 620 })
  await open(page)
  const title = unique('History')
  await newSpace(page, title)
  const space = await request.get('/api/v1/spaces').then((response) => response.json())
  const id = space.find((item: { title: string }) => item.title === title).id
  for (let index = 0; index < 60; index++) {
    await request.post(`/api/v1/spaces/${id}/messages`, {
      data: { client_message_id: crypto.randomUUID(), markdown: `Message ${index}` },
    })
  }
  await expect.poll(async () => (await (await request.get(`/api/v1/spaces/${id}/messages?limit=200`)).json()).items.length, { timeout: 60000 }).toBeGreaterThanOrEqual(120)
  await page.reload()
  await page.waitForSelector('nav')
  await page.waitForTimeout(800)
  const count = await page.locator('article').count()
  expect(count).toBe(50)
  const firstKey = await page.locator('article').first().getAttribute('data-message')
  await scroller(page).evaluate((node) => (node.scrollTop = 0))
  await expect.poll(() => page.locator('article').count()).toBeGreaterThan(50)
  const top = await page.locator(`article[data-message="${firstKey}"]`).evaluate((node, parent) => node.getBoundingClientRect().top - parent!.getBoundingClientRect().top, await scroller(page).elementHandle())
  expect(top).toBeGreaterThan(-40)
  expect(top).toBeLessThan(260)
})

test('screen readers hear completion, not tokens', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Announce'))
  await send(page, 'hello there')
  await expect(page.locator('section span[role="status"].sr')).toHaveText('Test agent replied', { timeout: 20000 })
  expect(await page.getByRole('log').getAttribute('aria-live')).toBe('off')
})

test('dragging a rail item reorders it, and a drag does not navigate', async ({ page }) => {
  await open(page)
  const labels = () => page.locator('[role="list"] button').evaluateAll((nodes) => nodes.map((n) => n.getAttribute('aria-label')!.split(',')[0]))
  const before = await labels()
  const current = await page.locator('header h1').textContent()
  const source = await page.getByRole('button', { name: new RegExp(`^${before[0]}`) }).boundingBox()
  const target = await page.getByRole('button', { name: new RegExp(`^${before[1]}`) }).boundingBox()
  await page.mouse.move(source!.x + 30, source!.y + source!.height / 2)
  await page.mouse.down()
  await page.mouse.move(source!.x + 30, source!.y + source!.height / 2 + 12, { steps: 3 })
  await page.mouse.move(target!.x + 30, target!.y + target!.height - 4, { steps: 6 })
  await page.mouse.up()
  await expect.poll(async () => (await labels())[1]).toBe(before[0])
  await expect(page.locator('header h1')).toHaveText(current!)
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect.poll(async () => (await labels())[0]).toBe(before[0])
})

test('General has no header, and the agent picture overlaps the top right corner of a nameless reply', async ({ page }) => {
  await open(page)
  await expect(page.getByRole('heading', { name: 'General' })).toHaveClass('sr')
  await expect(page.getByRole('button', { name: 'Space options' })).toHaveCount(0)
  await send(page, unique('picture'))
  const reply = page.locator('article[data-role="agent"]').last()
  await expect(reply.getByText('Received')).toBeVisible({ timeout: 10000 })
  await expect(reply.getByText('Test agent', { exact: true })).toHaveCount(0)
  // Read together: replies move on a spring, so two separate reads can come from different frames.
  const { card, picture } = await reply.evaluate((row) => {
    const box = (node: Element) => {
      const { left, right, top, bottom } = node.getBoundingClientRect()
      return { left, right, top, bottom }
    }
    return { card: box(row.querySelector('[aria-label="Test agent"]')!), picture: box(row.querySelector('[aria-hidden="true"]')!) }
  })
  expect(picture.left).toBeLessThan(card.right)
  expect(picture.right).toBeGreaterThan(card.right)
  expect(picture.top).toBeLessThan(card.top)
  expect(picture.bottom).toBeGreaterThan(card.top)

  await page.getByRole('button', { name: /^Settings/ }).click()
  await page.getByRole('switch', { name: 'Show agent name' }).click()
  await page.getByRole('button', { name: /^General/ }).click()
  await expect(page.locator('article[data-role="agent"]').last().getByText('Test agent', { exact: true })).toBeVisible()
  await page.evaluate(() => localStorage.removeItem('amsg.preferences'))
})

test('General reports a lost connection without moving the transcript', async ({ page, context }) => {
  await open(page)
  const before = (await scroller(page).boundingBox())!
  await page.route('**/api/**', (route) => route.abort())
  await context.setOffline(true)
  await send(page, unique('offline'))
  await expect(page.getByRole('status').filter({ hasText: /Offline|Reconnecting/ }).first()).toBeVisible({ timeout: 8000 })
  const during = (await scroller(page).boundingBox())!
  expect(during.y).toBe(before.y)
  expect(during.height).toBe(before.height)
  await context.setOffline(false)
  await page.unroute('**/api/**')
})
