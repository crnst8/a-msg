import { useEffect, useRef, useState } from 'react'
import { IconButton } from '@/components/ui'
import { useApp } from '@/state/queries'
import type { SearchHit } from '@/transport/operations'
import styles from './Conversation.module.css'

const hasHighlights = typeof CSS !== 'undefined' && 'highlights' in CSS

function paint(query: string): void {
  if (!hasHighlights) return
  const registry = (CSS as any).highlights
  registry.delete('find')
  const needle = query.trim().toLowerCase()
  if (!needle) return
  const ranges: Range[] = []
  for (const body of document.querySelectorAll('[data-message]')) {
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent!.toLowerCase()
      for (let at = text.indexOf(needle); at >= 0; at = text.indexOf(needle, at + needle.length)) {
        const range = new Range()
        range.setStart(node, at)
        range.setEnd(node, at + needle.length)
        ranges.push(range)
      }
    }
  }
  registry.set('find', new (window as any).Highlight(...ranges))
}

export function FindBar({
  spaceId,
  onReveal,
  onClose,
}: {
  spaceId: string
  onReveal: (messageId: string) => void
  onClose: () => void
}) {
  const { transport } = useApp()
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [index, setIndex] = useState(0)
  const [failed, setFailed] = useState(false)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => input.current?.focus(), [])
  useEffect(() => () => void (hasHighlights && (CSS as any).highlights.delete('find')), [])

  useEffect(() => {
    paint(query)
    if (!query.trim()) {
      setHits([])
      return
    }
    const timer = setTimeout(() => {
      transport
        .call('search', { query: { q: query, space_id: spaceId, type: 'message', limit: 40 } })
        .then((result) => {
          setFailed(false)
          setHits(result.items)
          setIndex(0)
          if (result.items[0]) onReveal(result.items[0].id)
        })
        .catch(() => setFailed(true))
    }, 180)
    return () => clearTimeout(timer)
  }, [query, spaceId, transport, onReveal])

  useEffect(() => {
    const timer = setTimeout(() => paint(query), 60)
    return () => clearTimeout(timer)
  }, [index, hits, query])

  const step = (delta: number) => {
    if (!hits.length) return
    const next = (index + delta + hits.length) % hits.length
    setIndex(next)
    onReveal(hits[next].id)
  }

  return (
    <div className={styles.find} role="search">
      <input
        ref={input}
        className={styles.findInput}
        value={query}
        aria-label="Find in conversation"
        placeholder="Find"
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault()
            step(event.shiftKey ? -1 : 1)
          } else if (event.key === 'Escape') {
            event.stopPropagation()
            onClose()
          }
        }}
      />
      <span className={styles.findCount} aria-live="polite">
        {failed ? 'Offline' : query.trim() ? (hits.length ? `${index + 1} of ${hits.length}` : 'None') : ''}
      </span>
      <IconButton icon="up" size={16} label="Previous match" disabled={!hits.length} onClick={() => step(-1)} />
      <IconButton icon="down" size={16} label="Next match" disabled={!hits.length} onClick={() => step(1)} />
      <IconButton icon="close" size={16} label="Close find" onClick={onClose} />
    </div>
  )
}
