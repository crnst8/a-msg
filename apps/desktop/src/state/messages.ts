import { useSyncExternalStore } from 'react'
import type { Message } from '@/transport/operations'
import type { OutboxItem } from '@/transport/types'

export type LocalState = 'queued_local' | 'sending' | 'send_failed'
export type Entry = Message & { key: string; local?: LocalState; localError?: string }

const PAGE = 50
const NONE: string[] = []

interface Timeline {
  keys: string[]
  nextBefore: number | null
  loaded: boolean
  loading: boolean
  /** Bumps when older messages are prepended, so the list can restore its scroll position. */
  prependCount: number
  /** Arrived before the first page returned. That page may predate them, so they are merged into it. */
  early: Set<string>
}

type Loader = (spaceId: string, before?: number) => Promise<{ items: Message[]; next_before: number | null }>

const order = (entry: Entry): number => (entry.local ? Number.MAX_SAFE_INTEGER : entry.seq)

/** Holds transcripts outside React so a token updates one card rather than the whole list. */
export class MessageStore {
  private timelines = new Map<string, Timeline>()
  private entries = new Map<string, Entry>()
  private ids = new Map<string, string>()
  private listListeners = new Map<string, Set<() => void>>()
  private entryListeners = new Map<string, Set<() => void>>()
  private loader: Loader = async () => {
    throw new Error('Message loader is not installed')
  }
  onGap: (spaceId: string) => void = () => {}

  setLoader(loader: Loader): void {
    this.loader = loader
  }

  timeline(spaceId: string): Timeline {
    let timeline = this.timelines.get(spaceId)
    if (!timeline) {
      timeline = { keys: NONE, nextBefore: null, loaded: false, loading: false, prependCount: 0, early: new Set() }
      this.timelines.set(spaceId, timeline)
    }
    return timeline
  }

  private notifyList(spaceId: string): void {
    for (const listener of this.listListeners.get(spaceId) ?? []) listener()
  }
  private notifyEntry(key: string): void {
    for (const listener of this.entryListeners.get(key) ?? []) listener()
  }

  subscribeList(spaceId: string, listener: () => void): () => void {
    const set = this.listListeners.get(spaceId) ?? new Set()
    this.listListeners.set(spaceId, set)
    set.add(listener)
    return () => set.delete(listener)
  }
  subscribeEntry(key: string, listener: () => void): () => void {
    const set = this.entryListeners.get(key) ?? new Set()
    this.entryListeners.set(key, set)
    set.add(listener)
    return () => set.delete(listener)
  }

  entry(key: string): Entry | undefined {
    return this.entries.get(key)
  }

  keyFor(id: string): string | undefined {
    return this.ids.get(id)
  }

  private place(timeline: Timeline, key: string): void {
    if (timeline.keys.includes(key)) return
    const entry = this.entries.get(key)!
    const position = order(entry)
    let index = timeline.keys.length
    while (index > 0 && order(this.entries.get(timeline.keys[index - 1])!) > position) index--
    timeline.keys = [...timeline.keys.slice(0, index), key, ...timeline.keys.slice(index)]
  }

  private resort(timeline: Timeline): void {
    timeline.keys = [...timeline.keys].sort((a, b) => order(this.entries.get(a)!) - order(this.entries.get(b)!))
  }

  upsert(message: Message): void {
    const key = message.client_message_id ?? message.id
    const existing = this.entries.get(key)
    if (existing && !existing.local && existing.revision > message.revision) return
    const timeline = this.timeline(message.space_id)
    const wasLocal = Boolean(existing?.local)
    this.entries.set(key, { ...message, key })
    this.ids.set(message.id, key)
    if (existing) {
      if (wasLocal) {
        this.resort(timeline)
        this.notifyList(message.space_id)
      }
      this.notifyEntry(key)
    } else if (timeline.loaded) {
      this.place(timeline, key)
      this.notifyList(message.space_id)
    } else {
      timeline.early.add(key)
    }
  }

  appendDelta(delta: {
    message_id: string
    space_id: string
    revision: number
    offset: number
    append: string
  }): void {
    const key = this.ids.get(delta.message_id)
    const current = key ? this.entries.get(key) : undefined
    if (!current || !key) return
    if (delta.revision <= current.revision) return
    if (delta.revision !== current.revision + 1 || delta.offset !== current.markdown.length) {
      this.onGap(delta.space_id)
      return
    }
    this.entries.set(key, { ...current, markdown: current.markdown + delta.append, revision: delta.revision })
    this.notifyEntry(key)
  }

  remove(spaceId: string, id: string): void {
    const key = this.ids.get(id) ?? id
    const timeline = this.timeline(spaceId)
    this.entries.delete(key)
    this.ids.delete(id)
    timeline.keys = timeline.keys.filter((candidate) => candidate !== key)
    this.notifyList(spaceId)
  }

  /** A message the service has not accepted yet: shown at once, then replaced in place by its receipt. */
  putLocal(item: OutboxItem): void {
    const state: LocalState =
      item.state === 'failed' ? 'send_failed' : item.state === 'sending' ? 'sending' : 'queued_local'
    const key = item.client_message_id
    const existing = this.entries.get(key)
    if (existing && !existing.local) return
    const timeline = this.timeline(item.space_id)
    this.entries.set(key, {
      key,
      local: state,
      localError: item.error,
      id: key,
      seq: 0,
      space_id: item.space_id,
      role: 'user',
      markdown: item.markdown,
      status: 'accepted',
      client_message_id: key,
      run_id: null,
      author: null,
      activity: null,
      attempt: 1,
      revision: 0,
      meta: {},
      artifacts: item.artifacts,
      links: [],
      created_at: item.created_at,
      updated_at: item.created_at,
    })
    if (existing) {
      this.notifyEntry(key)
    } else if (timeline.loaded) {
      this.place(timeline, key)
      this.notifyList(item.space_id)
    } else {
      timeline.early.add(key)
    }
  }

  async loadLatest(spaceId: string, pending: OutboxItem[] = []): Promise<void> {
    const timeline = this.timeline(spaceId)
    if (timeline.loading) return
    timeline.loading = true
    try {
      const page = await this.loader(spaceId)
      const keys: string[] = []
      for (const message of page.items) {
        const key = message.client_message_id ?? message.id
        const existing = this.entries.get(key)
        if (!existing || existing.local || existing.revision <= message.revision) {
          this.entries.set(key, { ...message, key })
          this.ids.set(message.id, key)
          if (existing) this.notifyEntry(key)
        }
        keys.push(key)
      }
      const previous = timeline.loaded ? timeline.keys : [...timeline.early].filter((key) => this.entries.has(key))
      const held = previous.filter((key) => !keys.includes(key))
      timeline.early.clear()
      const older = held.filter((key) => order(this.entries.get(key)!) < (page.items[0]?.seq ?? 0))
      const newer = held.filter((key) => !older.includes(key))
      timeline.keys = [...older, ...keys, ...newer]
      // Older pages already held keep their own cursor; otherwise this page defines how far back to go.
      if (!older.length) timeline.nextBefore = page.next_before
      timeline.loaded = true
      for (const item of pending) if (item.space_id === spaceId) this.putLocal(item)
      this.resort(timeline)
      this.notifyList(spaceId)
    } finally {
      timeline.loading = false
    }
  }

  async loadOlder(spaceId: string): Promise<boolean> {
    const timeline = this.timeline(spaceId)
    if (timeline.loading || timeline.nextBefore === null) return false
    timeline.loading = true
    try {
      const page = await this.loader(spaceId, timeline.nextBefore)
      const keys: string[] = []
      for (const message of page.items) {
        const key = message.client_message_id ?? message.id
        this.entries.set(key, { ...message, key })
        this.ids.set(message.id, key)
        keys.push(key)
      }
      timeline.keys = [...keys, ...timeline.keys]
      timeline.nextBefore = page.next_before
      timeline.prependCount++
      this.notifyList(spaceId)
      return true
    } finally {
      timeline.loading = false
    }
  }

  /** After a snapshot replacement, refresh loaded transcripts rather than patching them. */
  reloadLoaded(pending: OutboxItem[]): void {
    for (const [spaceId, timeline] of this.timelines) {
      if (timeline.loaded) void this.loadLatest(spaceId, pending)
    }
  }

  clear(): void {
    this.timelines.clear()
    this.entries.clear()
    this.ids.clear()
  }

  activeReply(spaceId: string): Entry | undefined {
    const keys = this.timeline(spaceId).keys
    for (let index = keys.length - 1; index >= 0; index--) {
      const entry = this.entries.get(keys[index])!
      if (entry.role === 'agent' && (entry.status === 'running' || entry.status === 'cancel_requested')) return entry
    }
    return undefined
  }

  pageSize(): number {
    return PAGE
  }
}

export const messages = new MessageStore()

export function useTimeline(spaceId: string): string[] {
  return useSyncExternalStore(
    (listener) => messages.subscribeList(spaceId, listener),
    () => messages.timeline(spaceId).keys,
  )
}

export function useTimelineMeta(spaceId: string): { loaded: boolean; hasOlder: boolean; prepends: number } {
  const loaded = useSyncExternalStore(
    (listener) => messages.subscribeList(spaceId, listener),
    () => messages.timeline(spaceId).loaded,
  )
  const hasOlder = useSyncExternalStore(
    (listener) => messages.subscribeList(spaceId, listener),
    () => messages.timeline(spaceId).nextBefore !== null,
  )
  const prepends = useSyncExternalStore(
    (listener) => messages.subscribeList(spaceId, listener),
    () => messages.timeline(spaceId).prependCount,
  )
  return { loaded, hasOlder, prepends }
}

export function useEntry(key: string): Entry | undefined {
  return useSyncExternalStore(
    (listener) => messages.subscribeEntry(key, listener),
    () => messages.entry(key),
  )
}

/** Re-evaluates when any card in the space changes state: used by the composer's Send/Stop slot. */
export function useActiveReply(spaceId: string): Entry | undefined {
  return useSyncExternalStore(
    (listener) => {
      const off = messages.subscribeList(spaceId, listener)
      const offActive = activeListeners.subscribe(spaceId, listener)
      return () => {
        off()
        offActive()
      }
    },
    () => messages.activeReply(spaceId),
  )
}

class ActiveListeners {
  private sets = new Map<string, Set<() => void>>()
  subscribe(spaceId: string, listener: () => void): () => void {
    const set = this.sets.get(spaceId) ?? new Set()
    this.sets.set(spaceId, set)
    set.add(listener)
    return () => set.delete(listener)
  }
  notify(spaceId: string): void {
    for (const listener of this.sets.get(spaceId) ?? []) listener()
  }
}

export const activeListeners = new ActiveListeners()
