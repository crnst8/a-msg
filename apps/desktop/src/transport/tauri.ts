import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import { ServiceError, type ArtifactRef, type CallArgs, type CallResult, type RouteName } from './operations'
import type { AppTransport, OutboxItem, OutboxResult, PairingInfo, SavedServer, StreamSignal, UploadSource } from './types'


function failure(error: unknown): ServiceError {
  const e = error as { status?: number; code?: string; message?: string; retryable?: boolean; details?: Record<string, unknown> }
  return new ServiceError(e.status ?? 0, e.code ?? 'error', e.message ?? String(error), e.retryable, e.details)
}

async function native<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  try {
    return await invoke<T>(command, args)
  } catch (error) {
    throw failure(error)
  }
}

interface NativeFile {
  path: string
  name: string
  size: number
}

/** Everything here crosses into the native core, which owns the token, remote I/O and local state. */
export class TauriTransport implements AppTransport {
  readonly kind = 'tauri' as const

  pairing(): Promise<PairingInfo> {
    return native('connection_status')
  }
  pair(endpoint: string, code: string, deviceName: string): Promise<PairingInfo> {
    return native('connection_pair', { endpoint, code, deviceName })
  }
  savedServers(): Promise<SavedServer[]> {
    return native('connection_servers')
  }
  leaveServer(): Promise<void> {
    return native('connection_leave')
  }
  switchServer(serviceId: string): Promise<PairingInfo> {
    return native('connection_switch', { serviceId })
  }
  signOut(serviceId?: string): Promise<boolean> {
    return native('connection_sign_out', { serviceId })
  }
  call<K extends RouteName>(name: K, args: CallArgs<K>): Promise<CallResult<K>> {
    const a = (args ?? {}) as { id?: string; query?: unknown; body?: unknown }
    return native('api_request', { route: name, id: a.id, query: a.query, body: a.body })
  }
  startStream(after: number): void {
    void native('stream_start', { after })
  }
  stopStream(): void {
    void native('stream_stop')
  }
  onSignal(listener: (signal: StreamSignal) => void): () => void {
    const stop = listen<StreamSignal>('amsg://stream', (event) => listener(event.payload))
    return () => void stop.then((off) => off())
  }
  async enqueue(item: Omit<OutboxItem, 'state' | 'created_at'>): Promise<void> {
    await native('outbox_enqueue', { item: { ...item, state: 'queued', created_at: new Date().toISOString() } })
  }
  outbox(): Promise<OutboxItem[]> {
    return native('outbox_list')
  }
  discard(id: string): Promise<void> {
    return native('outbox_discard', { id })
  }
  resend(id: string): Promise<void> {
    return native('outbox_resend', { id })
  }
  onOutbox(listener: (result: OutboxResult) => void): () => void {
    const stop = listen<OutboxResult>('amsg://outbox', (event) => listener(event.payload))
    return () => void stop.then((off) => off())
  }
  async upload(source: UploadSource, onProgress: (sent: number) => void, signal: AbortSignal): Promise<ArtifactRef> {
    const id = crypto.randomUUID()
    const name = source.name
    const path =
      source instanceof File
        ? await native<string>('upload_stage', new Uint8Array(await source.arrayBuffer()) as never)
        : source.path
    const stop = await listen<{ id: string; sent: number }>('amsg://upload', (event) => {
      if (event.payload.id === id) onProgress(event.payload.sent)
    })
    const cancel = () => void native('upload_cancel', { id })
    signal.addEventListener('abort', cancel)
    try {
      return await native('upload_file', { id, path, name })
    } finally {
      signal.removeEventListener('abort', cancel)
      stop()
    }
  }
  async fetchBytes(id: string, version?: number): Promise<Blob> {
    const bytes = await native<ArrayBuffer>('artifact_bytes', { id, version })
    return new Blob([bytes])
  }
  save(artifact: ArtifactRef, version?: number): Promise<string | null> {
    return native('artifact_save', { id: artifact.id, version, name: artifact.display_name })
  }
  async openFile(artifact: ArtifactRef, version?: number): Promise<boolean> {
    await native('artifact_open', { id: artifact.id, version, name: artifact.display_name })
    return true
  }
  reveal(path: string): Promise<void> {
    return native('artifact_reveal', { path })
  }
  openExternal(url: string): Promise<void> {
    return native('external_open', { url })
  }
  pickFiles(): Promise<UploadSource[]> {
    return native('files_pick')
  }
  notify(title: string, body: string): Promise<void> {
    return native('notification_send', { title, body })
  }
  localService(): Promise<{ available: boolean; running: boolean }> {
    return native('local_service_status')
  }
  startLocalService(): Promise<PairingInfo> {
    return native('local_service_start')
  }
  onFileDrop(listener: (files: UploadSource[], phase: 'over' | 'leave' | 'drop') => void): () => void {
    const stop = getCurrentWebview().onDragDropEvent(async (event) => {
      if (event.payload.type === 'drop') {
        listener(await native<NativeFile[]>('files_describe', { paths: event.payload.paths }), 'drop')
      } else if (event.payload.type === 'leave') {
        listener([], 'leave')
      } else if (event.payload.type === 'enter' || event.payload.type === 'over') {
        listener([], 'over')
      }
    })
    return () => void stop.then((off) => off())
  }
}
