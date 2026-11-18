import type { ArtifactRef, CallArgs, CallResult, Receipt, RouteName } from './operations'

export type LinkState = 'connecting' | 'online' | 'offline'

export interface ServiceEvent {
  seq: number
  schema_version: number
  type: string
  entity_id: string | null
  payload: Record<string, any>
  created_at: string
}

export interface OutboxItem {
  client_message_id: string
  space_id: string
  markdown: string
  artifacts: ArtifactRef[]
  created_at: string
  state: 'queued' | 'sending' | 'failed'
  error?: string
}

export interface PairingInfo {
  paired: boolean
  endpoint?: string
  service_id?: string
}

/** A server this device can return to without a new pairing code. */
export interface SavedServer {
  service_id: string
  endpoint: string
}

export type StreamSignal =
  | { kind: 'event'; event: ServiceEvent }
  | { kind: 'link'; state: LinkState }
  | { kind: 'resync' }
  | { kind: 'unauthorized' }

export type UploadSource = File | { path: string; name: string; size: number }

export interface OutboxResult {
  item: OutboxItem
  receipt?: Receipt
}

/**
 * Everything the interface may ask of the outside world. Components never call fetch. In the desktop app, tokens,
 * remote HTTP and streaming stay in the native core; in a browser, the credential is an HttpOnly cookie the page
 * cannot read (transport/web.ts).
 */
export interface AppTransport {
  readonly kind: 'dev' | 'tauri' | 'web'
  pairing(): Promise<PairingInfo>
  /** Pairs with a code, or on the web signs in with `password` and an authenticator code. */
  pair(endpoint: string, code: string, deviceName: string, password?: string): Promise<PairingInfo>
  savedServers(): Promise<SavedServer[]>
  /** Leaves the current server and keeps its credential. */
  leaveServer(): Promise<void>
  /** Returns to a saved server. */
  switchServer(serviceId: string): Promise<PairingInfo>
  /** Revokes this device and removes the credential: the current server, or a saved one by ID. Resolves true when the server confirmed. */
  signOut(serviceId?: string): Promise<boolean>
  call<K extends RouteName>(name: K, args: CallArgs<K>): Promise<CallResult<K>>
  /** Starts or restarts the event stream after a cursor. Signals arrive through onSignal. */
  startStream(after: number): void
  stopStream(): void
  onSignal(listener: (signal: StreamSignal) => void): () => void
  /** Durable send. Retries keep the original client message ID and survive restarts. */
  enqueue(item: Omit<OutboxItem, 'state' | 'created_at'>): Promise<void>
  outbox(): Promise<OutboxItem[]>
  discard(clientMessageId: string): Promise<void>
  resend(clientMessageId: string): Promise<void>
  onOutbox(listener: (result: OutboxResult) => void): () => void
  upload(source: UploadSource, onProgress: (sent: number) => void, signal: AbortSignal): Promise<ArtifactRef>
  /** Bytes for an inline preview. */
  fetchBytes(artifactId: string, version?: number): Promise<Blob>
  /** Resolves to the saved path where the platform reports one. */
  save(artifact: ArtifactRef, version?: number): Promise<string | null>
  /** Hands the file to the system's default application. False where there is no native shell. */
  openFile(artifact: ArtifactRef, version?: number): Promise<boolean>
  reveal(path: string): Promise<void>
  openExternal(url: string): Promise<void>
  /** Native file picker where one exists. Returns null in the browser, which uses an input element. */
  pickFiles(): Promise<UploadSource[] | null>
  notify(title: string, body: string, spaceId: string): Promise<void>
  /** Native file drops, when the platform provides them. */
  onFileDrop(listener: (files: UploadSource[], phase: 'over' | 'leave' | 'drop') => void): () => void
  /** Bundled service on this Mac, when the build includes one. */
  localService(): Promise<{ available: boolean; running: boolean }>
  startLocalService(): Promise<PairingInfo>
}
