import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject, type WheelEvent } from 'react'
import { messages } from '@/state/messages'

const FOLLOW_DISTANCE = 80
/** Trackpads report small stray deltas while a finger rests. These are not a reader leaving. */
const WHEEL_INTENT = 4

/** Where a reader stopped in each space. A space being followed has no entry and reopens at its end. */
const positions = new Map<string, number>()

type Motion = 'arrive' | 'grow' | 'none'

const calm = () =>
  document.documentElement.dataset.motion === 'reduce' || matchMedia('(prefers-reduced-motion: reduce)').matches

/** A spring from rest with about 1% overshoot. Sampled into keyframes because older WebKit has no linear(). */
const ARRIVAL_MS = 500
const ARRIVAL_STEPS = 24
const OMEGA = (2 * Math.PI) / 0.42
const DAMPING = 0.82
const DECAY = DAMPING * OMEGA
const RING = OMEGA * Math.sqrt(1 - DAMPING * DAMPING)
const spring = Array.from({ length: ARRIVAL_STEPS + 1 }, (_, index) => {
  if (index === ARRIVAL_STEPS) return 1
  const t = (index / ARRIVAL_STEPS) * (ARRIVAL_MS / 1000)
  return 1 - Math.exp(-DECAY * t) * (Math.cos(RING * t) + (DECAY / RING) * Math.sin(RING * t))
})

/**
 * scrollTop moves to its final value at once and the stack animates in from where it was, so the motion runs
 * on the compositor. Animations add, so a message arriving during another's motion continues it.
 */
function glide(stack: HTMLElement, shift: number, motion: Motion): void {
  if (motion === 'none' || Math.abs(shift) < 1 || calm()) return
  if (motion === 'arrive') {
    const frames = spring.map((done) => ({ transform: `translateY(${shift * (1 - done)}px)` }))
    stack.animate(frames, { duration: ARRIVAL_MS, composite: 'add' })
  } else {
    stack.animate([{ transform: `translateY(${shift}px)` }, { transform: 'translateY(0)' }], {
      duration: 180,
      easing: 'cubic-bezier(0.2, 0, 0, 1)',
      composite: 'add',
    })
  }
}

function enter(row: Element, rise: boolean): void {
  if (calm()) return
  row.animate(
    rise
      ? [{ opacity: 0, transform: 'translateY(14px)' }, { opacity: 1, transform: 'translateY(0)' }]
      : [{ opacity: 0 }, { opacity: 1 }],
    { duration: rise ? 260 : 160, easing: 'cubic-bezier(0.2, 0, 0, 1)' },
  )
}

interface Refs {
  scroller: RefObject<HTMLDivElement | null>
  content: RefObject<HTMLDivElement | null>
  stack: RefObject<HTMLDivElement | null>
}

/** Keeps the newest message in view until the reader moves away, and leaves the reader alone after that. */
export function useFollow(
  spaceId: string,
  keys: string[],
  meta: { loaded: boolean; hasOlder: boolean; prepends: number },
  { scroller, content, stack }: Refs,
) {
  const follow = useRef(true)
  const placed = useRef(false)
  const lastTop = useRef(0)
  const previous = useRef<string[]>([])
  const anchor = useRef<number | null>(null)
  const [detached, setDetached] = useState(false)

  const pin = useCallback(
    (motion: Motion): number => {
      const element = scroller.current!
      const from = lastTop.current
      element.scrollTop = element.scrollHeight
      const shift = element.scrollTop - from
      lastTop.current = element.scrollTop
      positions.delete(spaceId)
      const limit = element.clientHeight
      glide(stack.current!, Math.max(-limit, Math.min(limit, shift)), motion)
      return shift
    },
    [scroller, stack, spaceId],
  )

  const showState = useCallback(() => {
    const element = scroller.current!
    const distance = element.scrollHeight - element.clientHeight - element.scrollTop
    setDetached(!follow.current && distance > FOLLOW_DISTANCE)
  }, [scroller])

  useLayoutEffect(() => {
    const element = scroller.current
    const rows = stack.current
    if (!element || !rows || !meta.loaded) return
    const before = previous.current
    previous.current = keys
    if (!placed.current) {
      placed.current = true
      const saved = positions.get(spaceId)
      if (saved !== undefined) {
        element.scrollTop = saved
        follow.current = element.scrollHeight - element.clientHeight - saved <= FOLLOW_DISTANCE
      } else {
        element.scrollTop = element.scrollHeight
      }
      lastTop.current = element.scrollTop
      showState()
      return
    }
    const known = new Set(before)
    let added = 0
    while (added < keys.length && !known.has(keys[keys.length - 1 - added])) added++
    if (!added) return
    const shift = follow.current ? pin('arrive') : 0
    for (const row of [...rows.children].slice(-added)) enter(row, follow.current && shift < 1)
    showState()
  }, [meta.loaded, keys, spaceId, scroller, stack, pin, showState])

  // Text growing inside a card moves the list. A change to the scroller's own size, from the composer or the
  // window, happens at once and the list stays on it without motion.
  useEffect(() => {
    const body = content.current
    const element = scroller.current
    if (!body || !element) return
    let width = element.clientWidth
    const observer = new ResizeObserver((entries) => {
      const sameWidth = element.clientWidth === width
      width = element.clientWidth
      if (!placed.current) return
      if (follow.current) pin(sameWidth && entries.every((entry) => entry.target === body) ? 'grow' : 'none')
      else showState()
    })
    observer.observe(body)
    observer.observe(element)
    return () => observer.disconnect()
  }, [content, scroller, pin, showState])

  // A prepended page keeps the reader on the same message. WebKit has no scroll anchoring.
  useLayoutEffect(() => {
    const element = scroller.current
    if (element && anchor.current !== null) {
      element.scrollTop += element.scrollHeight - anchor.current
      lastTop.current = element.scrollTop
      anchor.current = null
    }
  }, [meta.prepends, scroller])

  const holdPosition = useCallback(() => {
    anchor.current = scroller.current?.scrollHeight ?? 0
  }, [scroller])
  const releasePosition = useCallback(() => {
    anchor.current = null
  }, [])

  const loadOlder = useCallback(() => {
    if (!meta.hasOlder) return
    holdPosition()
    void messages.loadOlder(spaceId).then((loaded) => {
      if (!loaded) releasePosition()
    })
  }, [meta.hasOlder, spaceId, holdPosition, releasePosition])

  const onScroll = () => {
    const element = scroller.current!
    const top = element.scrollTop
    const distance = element.scrollHeight - element.clientHeight - top
    // Scroll events arrive late, so content that grew meanwhile can look like distance from the end.
    // Only the reader moves upward: growth alone never ends following.
    const rising = top < lastTop.current - 1
    if (!rising && distance <= FOLLOW_DISTANCE) follow.current = true
    else if (rising && distance > FOLLOW_DISTANCE) follow.current = false
    lastTop.current = top
    if (follow.current) positions.delete(spaceId)
    else positions.set(spaceId, top)
    showState()
    if (top < 240 && meta.hasOlder) loadOlder()
  }

  // Scrolling up during a reply has to win straight away, before the next line pulls the list back down.
  const onWheel = (event: WheelEvent) => {
    const element = scroller.current!
    if (event.deltaY <= -WHEEL_INTENT && element.scrollHeight > element.clientHeight) follow.current = false
  }

  /** Back to the newest message. Sending does this too: a person's own message is never left below the fold. */
  const attach = useCallback(() => {
    follow.current = true
    setDetached(false)
    pin('arrive')
  }, [pin])

  return { detached, onScroll, onWheel, attach, holdPosition, releasePosition }
}
