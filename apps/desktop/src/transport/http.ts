import type { AppTransport, OutboxItem, OutboxResult, PairingInfo, SavedServer, StreamSignal, UploadSource } from './types'
import { ROUTES, ServiceError, resolvePath } from './operations'
import type { ArtifactRef, CallArgs, CallResult, Receipt, RouteName } from './operations'

const BACKOFF_MIN = 1000
const BACKOFF_MAX = 30000

/**
 * Same-origin HTTP transport for the browser. The credential is never visible to this page: the development proxy
 * adds it, or the browser sends the HttpOnly session cookie. Subclasses decide how a device connects.
 */
export abstract class HttpTransport implements AppTransport {
  abstract readonly kind: 'dev' | 'web'
  private signals = new Set<(signal: StreamSignal) => void>()
  private outboxListeners = new Set<(result: OutboxResult) => void>()
  private abort?: AbortController
  private generation = 0
  private items: OutboxItem[]
  private draining = false
  private retry?: ReturnType<typeof setTimeout>

  /** `base` prefixes every API path; `outboxKey` names this transport's stored outbox. */
  constructor(
    private readonly base: string,
    private readonly outboxKey: string,
  ) {
    this.items = this.loadOutbox()
    window.addEventListener('online', () => this.drain())
    queueMicrotask(() => this.drain())
  }

  abstract pairing(): Promise<PairingInfo>
  abstract pair(endpoint: string, code: string, deviceName: string): Promise<PairingInfo>
  abstract savedServers(): Promise<SavedServer[]>
  abstract leaveServer(): Promise<void>
  abstract switchServer(serviceId: string): Promise<PairingInfo>
  abstract signOut(serviceId?: string): Promise<boolean>

  protected url(path: string): string {
    return `${this.base}${path}`
  }

  protected async fetchJson(method: string, path: string, body?: unknown): Promise<any> {
    let response: Response
    try {
      response = await fetch(this.url(path), {
        method,
        headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      })
    } catch {
      throw new ServiceError(0, 'offline', 'The service is not reachable.', true)
    }
    if (response.status === 204) return null
    const text = await response.text()
    let data: any = null
    try {
      data = text ? JSON.parse(text) : null
    } catch {
      data = null
    }
    if (!response.ok) {
      const error = data?.error
      if (!error) throw new ServiceError(0, 'offline', 'The service is not reachable.', true)
      throw new ServiceError(response.status, error.code, error.message, error.retryable, error.details)
    }
    return data
  }

  call<K extends RouteName>(name: K, args: CallArgs<K>): Promise<CallResult<K>> {
    const [method, template] = ROUTES[name]
    const a = (args ?? {}) as { id?: string; query?: Record<string, unknown>; body?: unknown }
    return this.fetchJson(method, resolvePath(template, a.id, a.query), a.body)
  }

  onSignal(listener: (signal: StreamSignal) => void): () => void {
    this.signals.add(listener)
    return () => this.signals.delete(listener)
  }
  private signal(value: StreamSignal): void {
    for (const listener of this.signals) listener(value)
  }

  startStream(after: number): void {
    this.stopStream()
    const generation = ++this.generation
    const abort = new AbortController()
    this.abort = abort
    void this.streamLoop(after, generation, abort.signal)
  }
  stopStream(): void {
    this.generation++
    this.abort?.abort()
  }

  private async streamLoop(after: number, generation: number, signal: AbortSignal): Promise<void> {
    let cursor = after
    let delay = BACKOFF_MIN
    while (generation === this.generation) {
      this.signal({ kind: 'link', state: 'connecting' })
      try {
        const response = await fetch(this.url(`/v1/events?after=${cursor}`), { signal })
        if (response.status === 410) return this.signal({ kind: 'resync' })
        if (response.status === 401) return this.signal({ kind: 'unauthorized' })
        if (!response.ok || !response.body) throw new Error('stream failed')
        delay = BACKOFF_MIN
        const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
        let buffer = ''
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += value
          let boundary: number
          while ((boundary = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, boundary)
            buffer = buffer.slice(boundary + 2)
            if (generation !== this.generation) return
            const data = frame
              .split('\n')
              .filter((line) => line.startsWith('data: '))
              .map((line) => line.slice(6))
              .join('\n')
            if (frame.startsWith('event: ready')) {
              this.signal({ kind: 'link', state: 'online' })
              this.drain()
            } else if (data) {
              const event = JSON.parse(data)
              cursor = event.seq
              this.signal({ kind: 'event', event })
            }
          }
        }
      } catch {
        if (signal.aborted) return
      }
      if (generation !== this.generation) return
      this.signal({ kind: 'link', state: 'offline' })
      const wait = delay * (0.7 + Math.random() * 0.6)
      delay = Math.min(delay * 2, BACKOFF_MAX)
      await new Promise((resolve) => setTimeout(resolve, wait))
    }
  }

  // Outbox ---------------------------------------------------------------------------------------------
  private loadOutbox(): OutboxItem[] {
    try {
      const raw = localStorage.getItem(this.outboxKey)
      const parsed: OutboxItem[] = raw ? JSON.parse(raw) : []
      return parsed.map((item) => (item.state === 'sending' ? { ...item, state: 'queued' } : item))
    } catch {
      return []
    }
  }
  private persist(): void {
    try {
      localStorage.setItem(this.outboxKey, JSON.stringify(this.items))
    } catch {
      /* A full store leaves the item in memory for this session. */
    }
  }
  onOutbox(listener: (result: OutboxResult) => void): () => void {
    this.outboxListeners.add(listener)
    return () => this.outboxListeners.delete(listener)
  }
  private publish(result: OutboxResult): void {
    for (const listener of this.outboxListeners) listener(result)
  }
  async outbox(): Promise<OutboxItem[]> {
    return [...this.items]
  }
  async enqueue(item: Omit<OutboxItem, 'state' | 'created_at'>): Promise<void> {
    const entry: OutboxItem = { ...item, state: 'queued', created_at: new Date().toISOString() }
    this.items.push(entry)
    this.persist()
    this.publish({ item: entry })
    this.drain()
  }
  async discard(id: string): Promise<void> {
    this.items = this.items.filter((item) => item.client_message_id !== id)
    this.persist()
  }
  async resend(id: string): Promise<void> {
    this.items = this.items.map((item) =>
      item.client_message_id === id ? { ...item, state: 'queued', error: undefined } : item,
    )
    this.persist()
    this.drain()
  }

  private async drain(): Promise<void> {
    if (this.draining) return
    this.draining = true
    clearTimeout(this.retry)
    try {
      for (;;) {
        const item = this.items.find((entry) => entry.state === 'queued')
        if (!item) return
        item.state = 'sending'
        this.persist()
        this.publish({ item })
        try {
          const receipt: Receipt = await this.fetchJson('POST', `/v1/spaces/${item.space_id}/messages`, {
            client_message_id: item.client_message_id,
            markdown: item.markdown,
            artifact_ids: item.artifacts.map((artifact) => artifact.id),
          })
          this.items = this.items.filter((entry) => entry !== item)
          this.persist()
          this.signal({ kind: 'link', state: 'online' })
          this.publish({ item, receipt })
        } catch (error) {
          const failure = error as ServiceError
          if (failure.status === 401) {
            // The session ended. The message waits for the next sign-in instead of failing.
            item.state = 'queued'
            this.persist()
            this.publish({ item })
            this.signal({ kind: 'unauthorized' })
            return
          }
          if (failure.status === 0 || failure.retryable) {
            this.signal({ kind: 'link', state: 'offline' })
            item.state = 'queued'
            this.persist()
            this.publish({ item })
            this.retry = setTimeout(() => this.drain(), 4000)
            return
          }
          item.state = 'failed'
          item.error = failure.message
          this.persist()
          this.publish({ item })
        }
      }
    } finally {
      this.draining = false
    }
  }

  upload(source: UploadSource, onProgress: (sent: number) => void, signal: AbortSignal): Promise<ArtifactRef> {
    return new Promise((resolve, reject) => {
      if (!(source instanceof File)) return reject(new ServiceError(0, 'unsupported', 'Choose a file.'))
      const request = new XMLHttpRequest()
      const form = new FormData()
      form.append('file', source, source.name)
      request.open('POST', this.url('/v1/uploads'))
      request.responseType = 'json'
      request.upload.onprogress = (event) => onProgress(event.loaded)
      request.onload = () => {
        if (request.status >= 200 && request.status < 300) return resolve(request.response)
        const error = request.response?.error
        reject(new ServiceError(request.status, error?.code ?? 'upload_failed', error?.message ?? 'The upload failed.'))
      }
      request.onerror = () => reject(new ServiceError(0, 'offline', 'The upload was interrupted.', true))
      request.onabort = () => reject(new ServiceError(0, 'aborted', 'Upload cancelled.'))
      signal.addEventListener('abort', () => request.abort())
      request.send(form)
    })
  }

  async fetchBytes(id: string, version?: number): Promise<Blob> {
    const suffix = version ? `?version=${version}` : ''
    const response = await fetch(this.url(`/v1/artifacts/${id}/content${suffix}`))
    if (!response.ok) throw new ServiceError(response.status, 'file_unavailable', 'This file is not available.')
    return response.blob()
  }

  async openFile(): Promise<boolean> {
    return false
  }
  async reveal(): Promise<void> {}
  async save(artifact: ArtifactRef, version?: number): Promise<string | null> {
    const blob = await this.fetchBytes(artifact.id, version)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = artifact.display_name
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
    return null
  }

  async openExternal(url: string): Promise<void> {
    window.open(url, '_blank', 'noopener,noreferrer')
  }
  async pickFiles(): Promise<UploadSource[] | null> {
    return null
  }
  async notify(title: string, body: string): Promise<void> {
    if ('Notification' in window && Notification.permission === 'granted') new Notification(title, { body })
  }
  onFileDrop(): () => void {
    return () => {}
  }
  async localService(): Promise<{ available: boolean; running: boolean }> {
    return { available: false, running: false }
  }
  startLocalService(): Promise<PairingInfo> {
    return this.pairing()
  }
}
