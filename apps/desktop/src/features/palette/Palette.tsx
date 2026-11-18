import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigation, type Route } from '@/app/navigation'
import { SpaceIcon, Symbol } from '@/components/Symbol'
import type { SymbolName } from '@/components/symbols.gen'
import { fileSymbol } from '@/features/conversation/Message'
import { useApp, useSpaces } from '@/state/queries'
import type { SearchHit } from '@/transport/operations'
import styles from './Palette.module.css'

interface Item {
  id: string
  title: string
  detail?: React.ReactNode
  icon: React.ReactNode
  run: () => void
}

/** The search excerpt marks matches with control characters so no HTML is ever parsed. */
function Excerpt({ text }: { text: string }) {
  const parts = text.split(/\u0002|\u0003/)
  return (
    <>
      {parts.map((part, index) => (index % 2 ? <b key={index}>{part}</b> : <span key={index}>{part}</span>))}
    </>
  )
}

export function Palette({ onClose }: { onClose: () => void }) {
  const { transport } = useApp()
  const { navigate, show } = useNavigation()
  const spaces = useSpaces()
  const dialog = useRef<HTMLDialogElement>(null)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])
  const [failed, setFailed] = useState(false)
  const [searched, setSearched] = useState('')
  const [index, setIndex] = useState(0)

  useEffect(() => {
    dialog.current?.showModal()
    return () => dialog.current?.close()
  }, [])

  useEffect(() => {
    const text = query.trim()
    if (!text) {
      setHits([])
      setFailed(false)
      setSearched('')
      return
    }
    const timer = setTimeout(() => {
      transport
        .call('search', { query: { q: text, limit: 20 } })
        .then((result) => {
          setFailed(false)
          setHits(result.items.filter((hit) => hit.type !== 'space'))
          setSearched(text)
        })
        .catch(() => {
          setFailed(true)
          setSearched(text)
        })
    }, 160)
    return () => clearTimeout(timer)
  }, [query, transport])

  const items = useMemo(() => {
    const go = (route: Route) => () => {
      onClose()
      navigate(route)
    }
    const symbol = (name: SymbolName) => <Symbol name={name} size={20} />
    const needle = query.trim().toLowerCase()
    const matches = (text: string) => !needle || text.toLowerCase().includes(needle)
    const list: Item[] = []
    for (const space of spaces) {
      if (matches(space.title)) {
        list.push({
          id: `space:${space.id}`,
          title: space.title,
          icon: space.kind === 'general' ? symbol('thread') : <SpaceIcon icon={space.icon} size={20} />,
          run: go({ view: 'space', id: space.id }),
        })
      }
    }
    const actions: Item[] = [
      { id: 'new', title: 'New space', icon: symbol('plus'), run: () => show({ kind: 'new-space' }) },
      { id: 'library', title: 'Library', icon: symbol('fileText'), run: go({ view: 'library' }) },
      { id: 'code', title: 'Code Connector', icon: symbol('terminal'), run: go({ view: 'code' }) },
      { id: 'settings', title: 'Settings', icon: symbol('moreV'), run: go({ view: 'settings' }) },
      { id: 'help', title: 'Help', icon: symbol('search'), run: go({ view: 'help' }) },
      { id: 'archived', title: 'Archived spaces', icon: symbol('archive'), run: () => show({ kind: 'archive' }) },
    ]
    list.push(...actions.filter((action) => matches(action.title)))
    for (const hit of hits) {
      if (hit.type === 'message' && hit.space_id) {
        list.push({
          id: `message:${hit.id}`,
          title: hit.title,
          detail: <Excerpt text={hit.excerpt} />,
          icon: symbol('search'),
          run: go({ view: 'space', id: hit.space_id, focusMessage: hit.id }),
        })
      } else if (hit.type === 'artifact') {
        list.push({
          id: `artifact:${hit.id}`,
          title: hit.title,
          detail: 'File',
          icon: symbol(fileSymbol('', hit.title)),
          run: go({ view: 'library', artifactId: hit.id }),
        })
      }
    }
    return list.slice(0, 40)
  }, [spaces, hits, query, navigate, show, onClose])

  useEffect(() => setIndex(0), [items.length])

  const onKey = (event: React.KeyboardEvent) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setIndex((value) => Math.min(value + 1, items.length - 1))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setIndex((value) => Math.max(value - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      items[index]?.run()
    }
  }

  useEffect(() => {
    dialog.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [index])

  const nothing = query.trim() && !items.length && searched === query.trim()

  return (
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-label="Search"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onClick={(event) => {
        if (event.target === dialog.current) onClose()
      }}
    >
      <input
        className={styles.input}
        role="combobox"
        aria-expanded="true"
        aria-controls="palette-results"
        aria-label="Search spaces, messages, files and actions"
        placeholder="Search"
        autoFocus
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={onKey}
      />
      <div className={styles.results} id="palette-results" role="listbox">
        {nothing && (
          <p className={styles.message}>{failed ? 'Search needs a connection to the service.' : 'No results.'}</p>
        )}
        {failed && !nothing && <p className={styles.message}>Messages and files need a connection to the service.</p>}
        {items.map((item, position) => (
          <button
            key={item.id}
            type="button"
            role="option"
            aria-selected={position === index}
            className={styles.row}
            onMouseMove={() => setIndex(position)}
            onClick={item.run}
          >
            <span className={styles.rowIcon}>{item.icon}</span>
            <span className={styles.rowText}>
              <span className={styles.rowTitle}>{item.title}</span>
              {item.detail && <span className={styles.rowDetail}>{item.detail}</span>}
            </span>
          </button>
        ))}
      </div>
    </dialog>
  )
}
