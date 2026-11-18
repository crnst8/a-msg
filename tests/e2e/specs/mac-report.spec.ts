import { expect, test, type Page } from '@playwright/test'
import { article, composer, newSpace, open, scroller, send, unique } from './helpers'

const distance = (page: Page) =>
  scroller(page).evaluate((node) => node.scrollHeight - node.clientHeight - node.scrollTop)
const pill = (page: Page) => page.getByRole('button', { name: 'Jump to latest' })

async function fill(page: Page, count = 8) {
  for (let index = 0; index < count; index++) {
    await send(page, `Message ${index}\n\nsecond paragraph\n\nthird paragraph`)
    await expect(page.locator('article[data-role="agent"]')).toHaveCount(index + 1, { timeout: 8000 })
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0, { timeout: 8000 })
  }
}

test.describe('following the conversation', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 900, height: 560 })
    await open(page)
    await newSpace(page, unique('Follow'))
    await fill(page)
  })

  test('still follows when the transcript loads before the space list', async ({ page }) => {
    const hold = (route: any) => new Promise((done) => setTimeout(done, 500)).then(() => route.continue())
    await page.route('**/api/v1/snapshot', hold)
    await page.route('**/api/v1/spaces*', hold)
    await page.reload()
    await expect(article(page).first()).toBeVisible()
    await page.waitForTimeout(700)
    expect(await distance(page)).toBeLessThanOrEqual(1)
    await send(page, 'after a slow launch')
    await expect(page.locator('article[data-role="agent"]')).toHaveCount(9, { timeout: 8000 })
    await page.waitForTimeout(1500)
    expect(await distance(page)).toBeLessThanOrEqual(1)
    await expect(pill(page)).toHaveCount(0)
  })

  test('sending returns to the end after reading older messages', async ({ page }) => {
    await scroller(page).evaluate((node) => (node.scrollTop = 40))
    await expect(pill(page)).toBeVisible()
    await send(page, 'back to the end')
    await expect(pill(page)).toHaveCount(0)
    await expect(page.locator('article[data-role="agent"]')).toHaveCount(9, { timeout: 8000 })
    await page.waitForTimeout(1500)
    expect(await distance(page)).toBeLessThanOrEqual(1)
    await expect(pill(page)).toHaveCount(0)
  })

  test('new messages move the list up with an animation', async ({ page }) => {
    await page.waitForTimeout(500)
    const moved = page.evaluate(
      () =>
        new Promise<number>((done) => {
          const stack = document.querySelector('[role="log"] > div > div') as HTMLElement
          const start = performance.now()
          let most = 0
          const sample = () => {
            for (const animation of stack.getAnimations()) {
              const frame = (animation.effect as KeyframeEffect).getKeyframes()[0]
              most = Math.max(most, Number(/-?[\d.]+/.exec(String(frame.transform))?.[0] ?? 0))
            }
            if (performance.now() - start > 1500) done(most)
            else requestAnimationFrame(sample)
          }
          sample()
        }),
    )
    await send(page, 'watch this move')
    expect(await moved).toBeGreaterThan(40)
    // The scroll position is final at once, so nothing else depends on the motion finishing.
    await page.waitForTimeout(600)
    expect(await distance(page)).toBeLessThanOrEqual(1)
    expect(await scroller(page).evaluate((node) => node.scrollHeight - node.firstElementChild!.getBoundingClientRect().height)).toBeLessThanOrEqual(1)
  })

  test('reduced motion ends in the same place with no animation', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.waitForTimeout(500)
    const count = page.evaluate(
      () =>
        new Promise<number>((done) => {
          const stack = document.querySelector('[role="log"] > div > div') as HTMLElement
          const start = performance.now()
          let most = 0
          const sample = () => {
            most = Math.max(most, stack.getAnimations().length)
            if (performance.now() - start > 1000) done(most)
            else requestAnimationFrame(sample)
          }
          sample()
        }),
    )
    await send(page, 'no motion')
    expect(await count).toBe(0)
    expect(await distance(page)).toBeLessThanOrEqual(1)
  })

  test('a growing composer keeps the last message in view and does not detach', async ({ page }) => {
    await composer(page).click()
    for (let line = 0; line < 5; line++) {
      await page.keyboard.type('line')
      await page.keyboard.press('Shift+Enter')
      await page.waitForTimeout(40)
      expect(await distance(page)).toBeLessThanOrEqual(1)
    }
    await expect(pill(page)).toHaveCount(0)
    await page.keyboard.type('end')
    await page.keyboard.press('Enter')
    await page.waitForTimeout(1500)
    expect(await distance(page)).toBeLessThanOrEqual(1)
    await expect(pill(page)).toHaveCount(0)
  })

  test('scrolling up during a reply takes effect at once', async ({ page }) => {
    await send(page, 'slow job')
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toBeVisible({ timeout: 8000 })
    await page.mouse.move(450, 280)
    await page.mouse.wheel(0, -30)
    await page.waitForTimeout(400)
    const top = await scroller(page).evaluate((node) => node.scrollTop)
    await page.waitForTimeout(900)
    expect(await scroller(page).evaluate((node) => node.scrollTop)).toBe(top)
    await page.getByRole('button', { name: 'Stop', exact: true }).click()
  })

  test('a space being followed reopens at its end', async ({ page }) => {
    const title = await page.getByRole('heading', { level: 1 }).textContent()
    await page.getByRole('button', { name: /^General/ }).click()
    await page.getByRole('button', { name: new RegExp(`^${title}`) }).click()
    await expect(article(page).first()).toBeVisible()
    expect(await distance(page)).toBeLessThanOrEqual(1)
    await expect(pill(page)).toHaveCount(0)
  })
})

test.describe('macOS window chrome', () => {
  test.use({ userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15' })

  const lights = { right: 77, bottom: 22 }

  for (const position of ['Left', 'Right', 'Top', 'Bottom']) {
    test(`traffic lights stay on charcoal with the rail at ${position}`, async ({ page }) => {
      await open(page)
      await page.getByRole('button', { name: /^Settings/ }).click()
      await page.getByRole('radio', { name: position, exact: true }).click()
      const sheet = await page.locator('main').boundingBox()
      expect(sheet!.y > lights.bottom || sheet!.x > lights.right).toBe(true)
      const first = await page.locator('nav button').first().locator('img').boundingBox()
      expect(first!.y > lights.bottom || first!.x > lights.right).toBe(true)
      await page.getByRole('radio', { name: 'Left', exact: true }).click()
    })
  }

  test('every empty part of the rail drags the window', async ({ page }) => {
    await open(page)
    await expect(page.locator('nav')).toHaveAttribute('data-tauri-drag-region', 'deep')
    await expect(page.locator('nav button').first()).not.toHaveAttribute('data-tauri-drag-region')
  })
})

test.describe('changing server', () => {
  const connections = async (page: Page) => {
    await open(page)
    await page.getByRole('button', { name: /^Settings/ }).click()
    await page.getByRole('radio', { name: 'Connections' }).click()
  }

  test('no empty cards while offline', async ({ page }) => {
    await connections(page)
    await expect(page.getByText('(this device)')).toBeVisible()
    await page.getByRole('radio', { name: 'Appearance' }).click()
    await page.route('**/api/**', (route) => route.abort())
    await page.evaluate(() => window.dispatchEvent(new Event('offline')))
    await page.reload()
    await page.getByRole('button', { name: /^Settings/ }).click().catch(() => {})
    await page.getByRole('radio', { name: 'Connections' }).click()
    await expect(page.getByRole('button', { name: 'Change server' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
    await expect(page.getByText('(this device)')).toHaveCount(0)
    const cards = await page.evaluate(() =>
      [...document.querySelectorAll('main [class*="card"]')].map((card) => card.getBoundingClientRect().height),
    )
    expect(Math.min(...cards)).toBeGreaterThan(40)
  })

  test('Change server keeps the credential and one click returns', async ({ page }) => {
    await connections(page)
    await page.getByRole('button', { name: 'Change server' }).click()
    await expect(page.getByRole('heading', { name: 'Connect your agent' })).toBeVisible()
    await page.getByRole('button', { name: 'development proxy', exact: true }).click()
    await expect(page.getByRole('button', { name: /^General/ })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'General' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Settings' })).toHaveCount(0)
  })

  test('Sign out confirms, then needs a new pairing', async ({ page }) => {
    await connections(page)
    await page.getByRole('button', { name: 'Sign out' }).click()
    const sheet = page.getByRole('dialog')
    await expect(sheet.getByText('Reconnecting needs a new pairing code.')).toBeVisible()
    await sheet.getByRole('button', { name: 'Cancel' }).click()
    await expect(sheet).toHaveCount(0)
    await page.getByRole('button', { name: 'Sign out' }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Sign out' }).click()
    await expect(page.getByRole('heading', { name: 'Connect your agent' })).toBeVisible()
    await expect(page.getByRole('list', { name: 'Saved servers' })).toHaveCount(0)
    await page.getByLabel('Server address').fill('https://agent.example.net')
    await page.getByLabel('Pairing code').fill('AAAA-BBBB-CCCC')
    await page.getByRole('button', { name: 'Connect', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'General' })).toBeVisible()
    await send(page, 'working again after pairing')
    await expect(page.locator('article[data-role="agent"]').last()).toBeVisible({ timeout: 8000 })
  })

  test('a revoked device can pair again', async ({ page }) => {
    await open(page)
    let revoked = true
    await page.route('**/api/v1/snapshot', (route) =>
      revoked
        ? route.fulfill({ status: 401, contentType: 'application/json', body: '{"error":{"code":"unauthenticated","message":"Revoked"}}' })
        : route.continue(),
    )
    await page.reload()
    await expect(page.getByRole('alert')).toContainText('no longer paired')
    revoked = false
    await page.getByLabel('Server address').fill('https://agent.example.net')
    await page.getByLabel('Pairing code').fill('AAAA-BBBB-CCCC')
    await page.getByRole('button', { name: 'Connect', exact: true }).click()
    await expect(page.getByRole('button', { name: /^General/ })).toBeVisible()
  })
})

test('a pasted image shows a preview', async ({ page }) => {
  await open(page)
  await newSpace(page, unique('Paste'))
  await composer(page).click()
  await page.evaluate(async () => {
    const canvas = document.createElement('canvas')
    canvas.width = 240
    canvas.height = 160
    const context = canvas.getContext('2d')!
    context.fillStyle = '#7b7135'
    context.fillRect(0, 0, 240, 160)
    const blob = await new Promise<Blob>((done) => canvas.toBlob((b) => done(b!), 'image/png'))
    const data = new DataTransfer()
    data.items.add(new File([blob], 'pasted.png', { type: 'image/png' }))
    document.activeElement!.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true }))
  })
  const image = page.getByRole('img', { name: 'pasted.png' })
  await expect(image).toBeVisible()
  await expect.poll(() => image.evaluate((node: HTMLImageElement) => node.naturalWidth)).toBe(240)
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Remove pasted.png' }).click()
  await expect(image).toHaveCount(0)
})
