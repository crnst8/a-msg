import type { QueryClient } from '@tanstack/react-query'
import { useSyncExternalStore } from 'react'
import type { Automation, Connection, Message, Snapshot, Space } from '@/transport/operations'
import { ServiceError } from '@/transport/operations'
import type { AppTransport, LinkState, OutboxItem, ServiceEvent } from '@/transport/types'
import { activeListeners, messages } from './messages'
import { preferences } from './preferences'

export const keys = {
  spaces: ['spaces'],
  archived: ['spaces', 'archived'],
  connections: ['connections'],
  automations: ['automations'],
  artifacts: ['artifacts'],
  operation: (id: string) => ['operation', id],
  service: ['service'],
}

interface LinkInfo {
  state: LinkState
  serviceId: string | null
  queued: number
  unauthorized: boolean
  /** Null until the core has answered. */
  paired: boolean | null
}

let link: LinkInfo = { state: 'connecting', serviceId: null, queued: 0, unauthorized: false, paired: null }
const linkListeners = new Set<() => void>()
function setLink(patch: Partial<LinkInfo>): void {
  link = { ...link, ...patch }
  for (const listener of linkListeners) listener()
}
export function useLink(): LinkInfo {
  return useSyncExternalStore(
    (listener) => {
      linkListeners.add(listener)
      return () => linkListeners.delete(listener)
    },
    () => link,
  )
}

let spoken = ''
const spokenListeners = new Set<() => void>()
function setSpoken(text: string): void {
  spoken = text
  for (const listener of spokenListeners) listener()
}
/** One line for screen readers when a run ends. Streamed tokens are never announced. */
export function useSpoken(): string {
  return useSyncExternalStore(
    (listener) => {
      spokenListeners.add(listener)
      return () => spokenListeners.delete(listener)
    },
    () => spoken,
  )
}

type Listener = (event: { type: 'space_created_by_agent'; space: Space } | { type: 'space_deleted'; id: string }) => void

const sortSpaces = (spaces: Space[]) =>
  [...spaces].sort((a, b) => a.ordinal - b.ordinal || a.created_at.localeCompare(b.created_at))

/** Owns the path from the event stream into local state. Never resends accepted work. */
export class Sync {
  private pending: OutboxItem[] = []
  private expected = 0
  private listeners = new Set<Listener>()
  private started = false
  /** Bumps on leaving a server, so work still in flight for it is dropped. */
  private epoch = 0
  private unlisten: Array<() => void> = []
  currentSpace: string | null = null

  constructor(
    readonly transport: AppTransport,
    readonly client: QueryClient,
  ) {
    // Installed here, not in start(): a restored space opens before the parent's start effect runs.
    messages.setLoader((spaceId, before) =>
      this.transport.call('listMessages', { id: spaceId, query: { before, limit: messages.pageSize() } }),
    )
    messages.onGap = (spaceId) => void messages.loadLatest(spaceId, this.pending)
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
  private publish(event: Parameters<Listener>[0]): void {
    for (const listener of this.listeners) listener(event)
  }

  /** The next created space came from this user, so it may take focus. */
  expectSpace(): void {
    this.expected++
  }

  async boot(): Promise<void> {
    // A core that cannot answer at all leaves the connect screen, not a blank window.
    const info = await this.transport.pairing().catch(() => ({ paired: false }))
    setLink({ paired: info.paired })
  }

  private stop(): void {
    this.epoch++
    this.started = false
    for (const off of this.unlisten) off()
    this.unlisten = []
    this.transport.stopStream()
  }

  /** Runs the native step that ends the connection, then hands the window to the connect screen. */
  async disconnect<T>(step: () => Promise<T>): Promise<T> {
    const result = await step()
    this.stop()
    setLink({ paired: false })
    return result
  }

  /** The workspace is gone by now, so everything held for the previous server can be dropped. */
  connected(): void {
    this.stop()
    this.pending = []
    this.expected = 0
    messages.clear()
    this.client.clear()
    setLink({ state: 'connecting', serviceId: null, queued: 0, unauthorized: false, paired: true })
  }

  async start(): Promise<void> {
    if (this.started) return
    this.started = true
    const epoch = this.epoch
    this.unlisten = [
      this.transport.onSignal((signal) => {
        if (signal.kind === 'event') this.handle(signal.event)
        else if (signal.kind === 'link') setLink({ state: signal.state })
        else if (signal.kind === 'resync') void this.resync()
        else if (signal.kind === 'unauthorized') {
          this.transport.stopStream()
          setLink({ unauthorized: true, state: 'offline' })
        }
      }),
      this.transport.onOutbox(({ item, receipt }) => {
        this.pending = this.pending.filter((p) => p.client_message_id !== item.client_message_id)
        if (receipt) {
          messages.upsert(receipt.message)
        } else {
          this.pending.push(item)
          messages.putLocal(item)
        }
        setLink({ queued: this.pending.length })
      }),
    ]
    const pending = await this.transport.outbox()
    if (epoch !== this.epoch) return
    this.pending = pending
    setLink({ queued: pending.length })
    await this.resync()
  }

  async resync(): Promise<void> {
    const epoch = this.epoch
    try {
      const snapshot = await this.transport.call('snapshot', undefined)
      if (epoch !== this.epoch) return
      this.applySnapshot(snapshot)
      messages.reloadLoaded(this.pending)
      this.transport.startStream(snapshot.cursor)
    } catch (error) {
      if (epoch !== this.epoch) return
      setLink({ state: 'offline' })
      if ((error as ServiceError).status === 401) return setLink({ unauthorized: true })
      setTimeout(() => {
        if (epoch === this.epoch) void this.resync()
      }, 3000)
    }
  }

  private applySnapshot(snapshot: Snapshot): void {
    this.client.setQueryData(keys.spaces, sortSpaces(snapshot.spaces))
    this.client.setQueryData(keys.connections, snapshot.connections)
    this.client.setQueryData(keys.automations, snapshot.automations)
    setLink({ serviceId: snapshot.service_id, unauthorized: false })
  }

  private updateSpace(space: Space | undefined): void {
    if (!space) return
    this.client.setQueryData<Space[]>(keys.spaces, (list = []) => {
      if (space.archived_at) return list.filter((item) => item.id !== space.id)
      const next = list.some((item) => item.id === space.id)
        ? list.map((item) => (item.id === space.id ? space : item))
        : [...list, space]
      return sortSpaces(next)
    })
    this.client.invalidateQueries({ queryKey: keys.archived })
  }

  private touch(message: Message | undefined): void {
    if (!message) return
    messages.upsert(message)
    activeListeners.notify(message.space_id)
  }

  private handle(event: ServiceEvent): void {
    const p = event.payload
    switch (event.type) {
      case 'space.created':
        this.updateSpace(p.space)
        if (this.expected > 0) this.expected--
        else this.publish({ type: 'space_created_by_agent', space: p.space })
        break
      case 'space.updated':
      case 'space.archived':
        this.updateSpace(p.space)
        break
      case 'space.deleted':
        this.client.setQueryData<Space[]>(keys.spaces, (list = []) => list.filter((s) => s.id !== p.space_id))
        this.publish({ type: 'space_deleted', id: p.space_id })
        break
      case 'message.accepted':
      case 'message.updated':
        this.touch(p.message)
        this.updateSpace(p.space)
        break
      case 'message.removed':
        messages.remove(p.space_id, p.message_id)
        break
      case 'run.started':
        this.touch(p.message)
        break
      case 'run.delta':
        messages.appendDelta(p as any)
        break
      case 'run.completed':
      case 'run.failed':
      case 'run.cancelled':
        this.touch(p.request)
        this.touch(p.message)
        this.updateSpace(p.space)
        this.announce(event.type, p)
        if (p.space?.id === this.currentSpace && p.message) {
          const name = p.message.author ?? 'Agent'
          const outcome = event.type === 'run.completed' ? 'replied' : event.type === 'run.failed' ? 'failed' : 'stopped'
          setSpoken(`${name} ${outcome}`)
        }
        if (p.space?.id === this.currentSpace && document.visibilityState === 'visible') {
          this.markRead(p.space.id)
        }
        break
      case 'artifact.created':
      case 'artifact.updated':
        this.client.invalidateQueries({ queryKey: keys.artifacts })
        break
      case 'automation.updated':
        this.client.setQueryData<Automation[]>(keys.automations, p.automations)
        break
      case 'connection.updated':
        this.client.setQueryData<Connection[]>(keys.connections, (list = []) =>
          list.some((c) => c.id === p.connection.id)
            ? list.map((c) => (c.id === p.connection.id ? p.connection : c))
            : [...list, p.connection],
        )
        break
      case 'operation.updated':
        this.client.setQueryData(keys.operation(p.operation.id), p.operation)
        break
    }
  }

  private announce(type: string, payload: { space?: Space; message?: Message }): void {
    const { space, message } = payload
    if (!space || !message) return
    const inactive = document.visibilityState !== 'visible' || !document.hasFocus()
    const elsewhere = space.id !== this.currentSpace
    if (!inactive && !elsewhere) return
    const prefs = preferences()
    const failed = type === 'run.failed'
    if (failed ? !prefs.notifyFailures : !(elsewhere ? prefs.notifyCompleted : prefs.notifyReplies)) return
    const body = failed ? (message.error_message ?? 'The run failed.') : message.markdown.replace(/[#*`>_]/g, '').slice(0, 120)
    void this.transport.notify(space.title, body, space.id)
  }

  markRead(spaceId: string): void {
    void this.transport.call('readSpace', { id: spaceId }).catch(() => {})
  }

  openSpace(spaceId: string): void {
    this.currentSpace = spaceId
    const timeline = messages.timeline(spaceId)
    if (!timeline.loaded) void messages.loadLatest(spaceId, this.pending)
  }
}
