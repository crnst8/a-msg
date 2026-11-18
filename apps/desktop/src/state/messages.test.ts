import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Message } from '@/transport/operations'
import type { OutboxItem } from '@/transport/types'
import { MessageStore } from './messages'

const SPACE = 'space'

function message(partial: Partial<Message> & { id: string; seq: number }): Message {
  return {
    space_id: SPACE,
    role: 'agent',
    markdown: '',
    status: 'completed',
    client_message_id: null,
    run_id: null,
    author: 'Hermes',
    activity: null,
    attempt: 1,
    revision: 1,
    meta: {},
    artifacts: [],
    links: [],
    created_at: '2026-11-06T00:00:00Z',
    updated_at: '2026-11-06T00:00:00Z',
    ...partial,
  } as Message
}

const outbox = (id: string): OutboxItem => ({
  client_message_id: id,
  space_id: SPACE,
  markdown: 'hello',
  artifacts: [],
  created_at: '2026-11-06T00:00:00Z',
  state: 'queued',
})

describe('MessageStore', () => {
  let store: MessageStore
  beforeEach(async () => {
    store = new MessageStore()
    store.setLoader(async () => ({ items: [message({ id: 'a', seq: 1 })], next_before: null }))
    await store.loadLatest(SPACE)
  })

  it('drops older and duplicate revisions', () => {
    store.upsert(message({ id: 'a', seq: 1, revision: 3, markdown: 'new' }))
    store.upsert(message({ id: 'a', seq: 1, revision: 2, markdown: 'old' }))
    expect(store.entry('a')?.markdown).toBe('new')
  })

  it('applies a delta once and ignores a replay of it', () => {
    store.upsert(message({ id: 'b', seq: 2, status: 'running', revision: 1 }))
    const delta = { message_id: 'b', space_id: SPACE, revision: 2, offset: 0, append: 'Hi' }
    store.appendDelta(delta)
    store.appendDelta(delta)
    expect(store.entry('b')?.markdown).toBe('Hi')
    expect(store.entry('b')?.revision).toBe(2)
  })

  it('reloads the space on a gap instead of guessing', () => {
    const onGap = vi.fn()
    store.onGap = onGap
    store.upsert(message({ id: 'b', seq: 2, revision: 1 }))
    store.appendDelta({ message_id: 'b', space_id: SPACE, revision: 5, offset: 0, append: 'x' })
    expect(onGap).toHaveBeenCalledWith(SPACE)
    expect(store.entry('b')?.markdown).toBe('')
  })

  it('replaces a pending card in place, keeping its key', () => {
    store.putLocal(outbox('client-1'))
    expect(store.timeline(SPACE).keys).toEqual(['a', 'client-1'])
    expect(store.entry('client-1')?.local).toBe('queued_local')
    store.upsert(message({ id: 'server-1', seq: 2, role: 'user', client_message_id: 'client-1', status: 'queued_server' }))
    expect(store.timeline(SPACE).keys).toEqual(['a', 'client-1'])
    expect(store.entry('client-1')?.local).toBeUndefined()
    expect(store.entry('client-1')?.id).toBe('server-1')
  })

  it('does not downgrade an accepted message when the outbox reports late', () => {
    store.upsert(message({ id: 'server-1', seq: 2, role: 'user', client_message_id: 'client-1' }))
    store.putLocal(outbox('client-1'))
    expect(store.entry('client-1')?.local).toBeUndefined()
    expect(store.timeline(SPACE).keys).toEqual(['a', 'client-1'])
  })

  it('keeps pending messages after accepted ones', () => {
    store.putLocal(outbox('client-1'))
    store.upsert(message({ id: 'c', seq: 5 }))
    expect(store.timeline(SPACE).keys).toEqual(['a', 'c', 'client-1'])
  })

  it('prepends older pages and counts them for scroll restoration', async () => {
    store.setLoader(async () => ({ items: [message({ id: 'z', seq: 0 })], next_before: null }))
    store.timeline(SPACE).nextBefore = 1
    await store.loadOlder(SPACE)
    expect(store.timeline(SPACE).keys).toEqual(['z', 'a'])
    expect(store.timeline(SPACE).prependCount).toBe(1)
  })

  it('keeps a message sent before the first page returns', async () => {
    const fresh = new MessageStore()
    let finish: (page: { items: Message[]; next_before: null }) => void = () => {}
    fresh.setLoader(() => new Promise((resolve) => (finish = resolve)))
    const loading = fresh.loadLatest(SPACE)
    fresh.putLocal(outbox('client-1'))
    fresh.upsert(message({ id: 'server-1', seq: 1, role: 'user', client_message_id: 'client-1' }))
    fresh.upsert(message({ id: 'reply', seq: 2 }))
    finish({ items: [], next_before: null })
    await loading
    expect(fresh.timeline(SPACE).keys).toEqual(['client-1', 'reply'])
  })

  it('does not duplicate an early message the first page also contains', async () => {
    const fresh = new MessageStore()
    let finish: (page: { items: Message[]; next_before: null }) => void = () => {}
    fresh.setLoader(() => new Promise((resolve) => (finish = resolve)))
    const loading = fresh.loadLatest(SPACE)
    fresh.upsert(message({ id: 'b', seq: 2 }))
    finish({ items: [message({ id: 'a', seq: 1 }), message({ id: 'b', seq: 2 })], next_before: null })
    await loading
    expect(fresh.timeline(SPACE).keys).toEqual(['a', 'b'])
  })

  it('takes the older-page cursor from a reload', async () => {
    const fresh = new MessageStore()
    fresh.setLoader(async () => ({ items: [], next_before: null }))
    await fresh.loadLatest(SPACE)
    fresh.setLoader(async () => ({ items: [message({ id: 'n', seq: 90 })], next_before: 90 }))
    await fresh.loadLatest(SPACE)
    expect(fresh.timeline(SPACE).nextBefore).toBe(90)
  })

  it('keeps its cursor when older pages are already held', async () => {
    store.setLoader(async () => ({ items: [message({ id: 'z', seq: 0 })], next_before: 7 }))
    store.timeline(SPACE).nextBefore = 1
    await store.loadOlder(SPACE)
    store.setLoader(async () => ({ items: [message({ id: 'a', seq: 1 })], next_before: 99 }))
    await store.loadLatest(SPACE)
    expect(store.timeline(SPACE).nextBefore).toBe(7)
  })

  it('notifies only the card that changed', () => {
    store.upsert(message({ id: 'b', seq: 2, revision: 1 }))
    const list = vi.fn()
    const card = vi.fn()
    const other = vi.fn()
    store.subscribeList(SPACE, list)
    store.subscribeEntry('b', card)
    store.subscribeEntry('a', other)
    store.appendDelta({ message_id: 'b', space_id: SPACE, revision: 2, offset: 0, append: 'x' })
    expect(card).toHaveBeenCalledTimes(1)
    expect(list).not.toHaveBeenCalled()
    expect(other).not.toHaveBeenCalled()
  })

  it('finds the active reply', () => {
    store.upsert(message({ id: 'b', seq: 2, status: 'running' }))
    expect(store.activeReply(SPACE)?.id).toBe('b')
    store.upsert(message({ id: 'b', seq: 2, status: 'completed', revision: 2 }))
    expect(store.activeReply(SPACE)).toBeUndefined()
  })
})
