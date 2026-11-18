import { preferences } from './preferences'

export function pushSupported(): boolean {
  return window.isSecureContext && 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

async function api<T>(method: string, body?: unknown): Promise<T> {
  const response = await fetch('/v1/web/push', {
    method, credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) throw new Error('Could not save notification settings. Please try again.')
  return response.status === 204 ? undefined as T : response.json()
}

export async function pushStatus(): Promise<{ public_key: string; enabled: boolean }> {
  return api('GET')
}

export async function savePushPreferences(): Promise<void> {
  if (!pushSupported() || Notification.permission !== 'granted') return
  const registration = await navigator.serviceWorker.getRegistration('/')
  const subscription = await registration?.pushManager.getSubscription()
  if (subscription) await api('POST', { subscription: subscription.toJSON(), preferences: preferences() })
}

export async function enablePush(): Promise<void> {
  // Call synchronously from the button gesture; Safari requires user activation.
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') throw new Error('Notifications are blocked. Allow them in your device’s notification settings.')
  const { public_key } = await pushStatus()
  if (!public_key) throw new Error('Push notifications are unavailable on this server.')
  await navigator.serviceWorker.register('/sw.js', { scope: '/' })
  const registration = await navigator.serviceWorker.ready
  const decoded = atob(public_key.replace(/-/g, '+').replace(/_/g, '/'))
  const applicationServerKey = Uint8Array.from(decoded, char => char.charCodeAt(0))
  const subscription = await registration.pushManager.getSubscription() ??
    await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey })
  await api('POST', { subscription: subscription.toJSON(), preferences: preferences() })
}

export async function disablePush(): Promise<void> {
  await api('DELETE')
  const registration = await navigator.serviceWorker.getRegistration('/')
  await (await registration?.pushManager.getSubscription())?.unsubscribe()
}
