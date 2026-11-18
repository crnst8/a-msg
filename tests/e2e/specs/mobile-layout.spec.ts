import { expect, test } from '@playwright/test'
import { composer, noPageOverflow, open } from './helpers'

test.use({ hasTouch: true, isMobile: true })

for (const width of [320, 375, 390]) {
  test(`phone chat uses available space at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 })
    await open(page)
    await noPageOverflow(page)
    await expect(composer(page)).not.toBeFocused()
    const bounds = await page.getByRole('log').boundingBox()
    expect(bounds!.width).toBe(width)
    expect(bounds!.height).toBeGreaterThan(650)
    const send = await page.getByRole('button', { name: 'Send', exact: true }).boundingBox()
    expect(send!.width).toBeGreaterThanOrEqual(44)
    expect(send!.height).toBeGreaterThanOrEqual(44)
    await expect(page.getByRole('button', { name: 'Settings', exact: true })).toBeInViewport()
    await expect(page.getByRole('button', { name: 'New space', exact: true })).toBeInViewport()
  })
}

test('keyboard leaves the composer visible and rail out of the tab order', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript(() => {
    Object.defineProperty(window.visualViewport!, 'height', { configurable: true, get: () => 400 })
  })
  await open(page)
  await composer(page).focus()
  await page.evaluate(() => window.visualViewport!.dispatchEvent(new Event('resize')))
  await expect(page.locator('html')).toHaveAttribute('data-keyboard', 'true')
  await expect(page.getByRole('navigation')).toBeHidden()
  const input = await composer(page).boundingBox()
  expect(input!.y + input!.height).toBeLessThanOrEqual(400)
  await noPageOverflow(page)
})
