import { expect, test } from '@playwright/test'
import { composer, noPageOverflow, open } from './helpers'

test('Help sits above Settings and preserves the conversation draft', async ({ page }) => {
  await open(page)
  await composer(page).fill('An unfinished question')
  const help = page.getByRole('button', { name: 'Help', exact: true })
  const settings = page.getByRole('button', { name: 'Settings', exact: true })
  expect((await help.boundingBox())!.y).toBeLessThan((await settings.boundingBox())!.y)
  await help.click()
  await expect(page.getByRole('heading', { name: 'Help', exact: true })).toBeFocused()
  await expect(help).toHaveAttribute('aria-current', 'page')
  const first = page.locator('summary').first()
  await first.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByText('Enter sends your message. Shift + Enter adds a new line.')).toBeVisible()
  await page.getByRole('button', { name: 'Open General', exact: true }).click()
  await expect(composer(page)).toHaveValue('An unfinished question')
  await composer(page).fill('')
})

test('Help searches instructions, handles no matches and opens the relevant view', async ({ page }) => {
  await open(page)
  await page.getByRole('button', { name: 'Help', exact: true }).click()
  const search = page.getByRole('searchbox', { name: 'Search help' })
  await search.fill('paperclip')
  await expect(page.getByRole('status').filter({ hasText: '2 topics found' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Open Library', exact: true })).toBeVisible()
  await search.fill('no-such-help-topic')
  await expect(page.getByText(/No matching topics/)).toBeVisible()
  await page.getByRole('button', { name: 'Show all topics' }).click()
  await expect(page.locator('details')).toHaveCount(11)
  await search.fill('server unreachable')
  await expect(page.getByRole('button', { name: 'Check Connections' })).toBeVisible()
  await page.getByRole('button', { name: 'Check Connections' }).click()
  await expect(page.getByRole('radio', { name: 'Connections', exact: true })).toHaveAttribute('aria-checked', 'true')
})

test('Help and every phone rail control fit at 320px and 390px', async ({ page }) => {
  for (const width of [320, 390]) {
    await page.setViewportSize({ width, height: 844 })
    await open(page)
    await page.getByRole('button', { name: 'Help', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Start here' })).toBeVisible()
    await noPageOverflow(page)
    const bounds = await page.locator('nav button').evaluateAll((buttons) => buttons.map((button) => {
      const { left, right, width, height } = button.getBoundingClientRect()
      return { left, right, width, height }
    }))
    for (const box of bounds) {
      expect(box.left).toBeGreaterThanOrEqual(0)
      expect(box.right).toBeLessThanOrEqual(width)
      expect(box.width).toBeGreaterThanOrEqual(36)
      expect(box.height).toBeGreaterThanOrEqual(44)
    }
    for (let index = 1; index < bounds.length; index++) {
      expect(bounds[index].left).toBeGreaterThanOrEqual(bounds[index - 1].right)
    }
    await page.locator('summary').filter({ hasText: 'Use a-msg on your phone' }).click()
    await expect(page.getByText(/Share → Add to Home Screen/)).toBeVisible()
    await noPageOverflow(page)
  }
})
