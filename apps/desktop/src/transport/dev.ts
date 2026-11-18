import type { PairingInfo, SavedServer } from './types'
import { HttpTransport } from './http'

/** Browser development transport: the Vite proxy holds the token, so nothing secret reaches this page. */
export class DevTransport extends HttpTransport {
  readonly kind = 'dev' as const

  constructor() {
    super('/api', 'amsg.dev.outbox')
  }

  // The proxy holds the only credential, so the connection screens are simulated here and never revoke it.
  private paired = true
  private saved: SavedServer[] = []

  async pairing(): Promise<PairingInfo> {
    return this.paired ? { paired: true, endpoint: 'development proxy' } : { paired: false }
  }
  async pair(): Promise<PairingInfo> {
    this.paired = true
    this.saved = []
    return this.pairing()
  }
  async savedServers(): Promise<SavedServer[]> {
    return this.saved
  }
  async leaveServer(): Promise<void> {
    this.paired = false
    this.saved = [{ service_id: 'development', endpoint: 'development proxy' }]
  }
  async switchServer(): Promise<PairingInfo> {
    return this.pair()
  }
  async signOut(serviceId?: string): Promise<boolean> {
    if (!serviceId) this.paired = false
    this.saved = []
    return true
  }
}
