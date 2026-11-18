import { expect, test } from '@playwright/test'
import { open } from './helpers'

test('push worker formats an update and routes its conversation', async ({ page, context }) => {
  await context.grantPermissions(['notifications'])
  await open(page)
  const spaces = await (await page.request.get('/api/v1/spaces')).json()
  const target = spaces.find((space: { kind: string }) => space.kind === 'thread')
  expect(target).toBeTruthy()
  await page.evaluate(async () => {
    await navigator.serviceWorker.register('/sw.js')
    await navigator.serviceWorker.ready
  })
  await expect.poll(() => context.serviceWorkers().length).toBeGreaterThan(0)
  const worker = context.serviceWorkers()[0]
  // This headless Linux host has no notification daemon. Capture the browser display call,
  // then exercise the real worker handlers and window navigation.
  const shown = await worker.evaluate(async (spaceId) => {
    const scope = self as any
    let shown: { title: string; options: NotificationOptions } | null = null
    scope.registration.showNotification = async (title: string, options: NotificationOptions) => { shown = { title, options } }
    let pending = Promise.resolve()
    const event = new Event('push')
    Object.assign(event, {
      data: { json: () => ({ title: 'a-msg', body: 'Your agent has replied.', spaceId, tag: 'test-reply' }) },
      waitUntil: (promise: Promise<void>) => { pending = promise },
    })
    self.dispatchEvent(event)
    await pending
    return shown
  }, target.id)
  expect(shown).toMatchObject({ title: 'a-msg', options: { body: 'Your agent has replied.', data: { spaceId: target.id } } })
  await worker.evaluate(async (spaceId) => {
    let pending = Promise.resolve()
    const event = new Event('notificationclick')
    Object.assign(event, {
      notification: { data: { spaceId }, close() {} },
      waitUntil: (promise: Promise<void>) => { pending = promise },
    })
    self.dispatchEvent(event)
    // A synthetic click has no OS user activation; Chromium permits navigation but rejects focus.
    try { await pending } catch (error) {
      if (!(error instanceof DOMException) || error.name !== 'InvalidAccessError') throw error
    }
  }, target.id)
  await expect(page.getByRole('heading', { name: target.title, exact: true })).toBeVisible()
  await expect.poll(() => page.evaluate(() => location.search)).toBe('')
})
