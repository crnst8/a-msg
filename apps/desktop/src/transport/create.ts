import type { AppTransport } from './types'

/** Picks the transport for this runtime. The development transport is compiled out of release builds. */
export async function createTransport(): Promise<AppTransport> {
  if ('__TAURI_INTERNALS__' in window) {
    const { TauriTransport } = await import('./tauri')
    return new TauriTransport()
  }
  if (import.meta.env.DEV || import.meta.env.VITE_AMSG_HARNESS === '1') {
    const { DevTransport } = await import('./dev')
    return new DevTransport()
  }
  const { WebTransport } = await import('./web')
  return new WebTransport()
}
