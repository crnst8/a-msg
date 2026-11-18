/* Push only: authenticated pages and API responses are never cached. */
self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()))
self.addEventListener('push', event => {
  let data = {}
  try { data = event.data?.json() ?? {} } catch { /* Still display every received push. */ }
  event.waitUntil(self.registration.showNotification(data.title || 'a-msg', {
    body: data.body || 'You have an update.',
    icon: '/assets/branding/icon-192.png',
    tag: data.tag || 'amsg-update',
    data: { spaceId: data.spaceId },
  }))
})
self.addEventListener('notificationclick', event => {
  event.notification.close()
  const spaceId = event.notification.data?.spaceId
  const url = new URL('/', self.location.origin)
  if (typeof spaceId === 'string') url.searchParams.set('space', spaceId)
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    const client = windows.find(client => new URL(client.url).origin === url.origin)
    if (client) {
      await client.navigate(url.href)
      await client.focus()
    } else await self.clients.openWindow(url.href)
  })())
})
