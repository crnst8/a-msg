import { pushSupported } from '@/state/push'
import type { PairingInfo, SavedServer } from './types'
import { HttpTransport } from './http'
import { ServiceError } from './operations'

const SIGNED_IN_KEY = 'amsg.web.signed-in'

interface WebSession {
  configured: boolean
  signed_in: boolean
  service_id: string | null
  device_id: string | null
}

/** A short device name for the server's device list, such as "Safari on iPhone". */
export function browserName(agent = navigator.userAgent): string {
  const device = /iPhone/.test(agent)
    ? 'iPhone'
    : /iPad/.test(agent) || (/Macintosh/.test(agent) && navigator.maxTouchPoints > 1)
      ? 'iPad'
      : /Android/.test(agent)
        ? 'Android'
        : /Mac OS X/.test(agent)
          ? 'Mac'
          : /Windows/.test(agent)
            ? 'Windows'
            : /Linux/.test(agent)
              ? 'Linux'
              : 'device'
  const browser = /Edg\//.test(agent)
    ? 'Edge'
    : /Firefox\/|FxiOS/.test(agent)
      ? 'Firefox'
      : /Chrome\/|CriOS/.test(agent)
        ? 'Chrome'
        : /Safari\//.test(agent)
          ? 'Safari'
          : 'Browser'
  return `${browser} on ${device}`
}

/**
 * The page served by the a-msg service itself. Signing in with the password and an authenticator code gives this
 * browser a device credential in an HttpOnly cookie, which scripts on the page cannot read.
 */
export class WebTransport extends HttpTransport {
  readonly kind = 'web' as const

  constructor() {
    super('', 'amsg.web.outbox')
  }

  private session(): Promise<WebSession> {
    return this.fetchJson('GET', '/v1/web/session')
  }

  async pairing(): Promise<PairingInfo> {
    try {
      const session = await this.session()
      this.remember(session.signed_in)
      return { paired: session.signed_in, endpoint: location.host, service_id: session.service_id ?? undefined }
    } catch (error) {
      // Unreachable at launch: a browser that has used the workspace opens it from what it has, as the desktop app
      // does; one that has not gets the sign-in screen, which reports the outage.
      if ((error as ServiceError).status === 0) return { paired: this.usedBefore(), endpoint: location.host }
      throw error
    }
  }

  private usedBefore(): boolean {
    try {
      return localStorage.getItem(SIGNED_IN_KEY) === '1'
    } catch {
      return false
    }
  }

  private remember(signedIn: boolean): void {
    try {
      if (signedIn) localStorage.setItem(SIGNED_IN_KEY, '1')
      else localStorage.removeItem(SIGNED_IN_KEY)
    } catch {
      /* Without storage, an offline launch shows the sign-in screen. */
    }
  }

  /** Signs in with the password and a current authenticator code. */
  async pair(_endpoint: string, code: string, _deviceName: string, password = ''): Promise<PairingInfo> {
    await this.fetchJson('POST', '/v1/web/sign-in', { password, code, device_name: browserName() })
    return this.pairing()
  }

  async notify(title: string, body: string): Promise<void> {
    // The server delivers background Web Push; avoid a duplicate from the event stream.
    if (!pushSupported()) return
    const registration = await navigator.serviceWorker.getRegistration('/')
    if (await registration?.pushManager.getSubscription()) return
    if (Notification.permission !== 'granted') return
    if (registration) await registration.showNotification(title, { body })
    else return super.notify(title, body)
  }

  async savedServers(): Promise<SavedServer[]> {
    return []
  }
  async leaveServer(): Promise<void> {
    await this.signOut()
  }
  async switchServer(): Promise<PairingInfo> {
    return this.pairing()
  }
  async signOut(): Promise<boolean> {
    this.remember(false)
    try {
      await this.fetchJson('POST', '/v1/web/sign-out')
      return true
    } catch (error) {
      if (error instanceof ServiceError && error.status === 401) return true
      return false
    }
  }
}
