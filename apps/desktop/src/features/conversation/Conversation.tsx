import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigation } from '@/app/navigation'
import { Symbol } from '@/components/Symbol'
import { Button, IconButton, Menu, useMenu, type MenuEntry } from '@/components/ui'
import { ACCESS_NAMES, accessOptions, sessionAccess, type Access } from '@/features/code/access'
import { useArchiveSpace } from '@/features/rail/Rail'
import { messages, useTimeline, useTimelineMeta } from '@/state/messages'
import { useApp, useAutomations, useConnections, useSpaces } from '@/state/queries'
import { useLink, useSpoken } from '@/state/sync'
import { reportError } from '@/state/toasts'
import type { Connection, Space } from '@/transport/operations'
import { Composer, type ComposerHandle } from './Composer'
import styles from './Conversation.module.css'
import composerStyles from './Composer.module.css'
import { FindBar } from './FindBar'
import { MessageRow } from './Message'
import { useFollow } from './useFollow'

function Header({
  space,
  connection,
  onFind,
}: {
  space: Space
  connection: Connection | undefined
  onFind: () => void
}) {
  const { show } = useNavigation()
  const { transport } = useApp()
  const link = useLink()
  const automations = useAutomations().filter((job) => job.space_id === space.id)
  const archive = useArchiveSpace()
  const menu = useMenu()
  const reachable = link.state === 'online' && connection?.state !== 'disconnected' && connection?.state !== 'error'
  const dot = link.state === 'connecting' ? 'connecting' : reachable ? 'online' : 'offline'
  const status =
    link.state === 'offline'
      ? `Offline${link.queued ? `, ${link.queued} queued` : ''}`
      : link.state === 'connecting'
        ? 'Reconnecting'
        : connection?.state === 'disconnected'
          ? `${connection.name} offline`
          : (connection?.name ?? 'No agent')

  const automationItems = automations.map(
    (automation): MenuEntry => ({
      label: automation.name,
      icon: 'clockBadge',
      onSelect: () => show({ kind: 'automation', automation }),
    }),
  )
  const options = space.kind === 'code' ? accessOptions(connection) : null
  const access = space.kind === 'code' ? sessionAccess(space, connection) : null
  const setAccess = (level: Access) =>
    transport
      .call('patchSpace', { id: space.id, body: { expected_revision: space.revision, access: level } })
      .catch(reportError)
  const accessItems: MenuEntry[] = options
    ? [
        { rule: true },
        ...options.levels.map(
          (level): MenuEntry => ({
            label: ACCESS_NAMES[level],
            checked: level === access,
            onSelect: () => level !== access && setAccess(level),
          }),
        ),
      ]
    : []
  const items: MenuEntry[] = [
    { label: 'Find in conversation', icon: 'search', onSelect: onFind },
    ...(space.kind === 'general'
      ? []
      : ([
          { label: 'Rename', icon: 'rename', onSelect: () => show({ kind: 'rename', space }) },
          { label: 'Change icon', icon: 'star', onSelect: () => show({ kind: 'icon', space }) },
        ] as MenuEntry[])),
    ...accessItems,
    ...automationItems,
    ...(space.kind === 'general'
      ? []
      : ([
          { rule: true },
          { label: 'Archive', icon: 'archive', onSelect: () => archive(space) },
          { label: 'Delete', icon: 'trash', danger: true, onSelect: () => show({ kind: 'delete', space }) },
        ] as MenuEntry[])),
  ]
  const directory = space.session?.working_directory
  if (space.kind === 'general') {
    return (
      <header className={styles.floating}>
        <h1 className="sr">{space.title}</h1>
        {dot !== 'online' && (
          <span className={styles.problem} role="status">
            <span className={styles.dot} data-state={dot} />
            {status}
          </span>
        )}
        {automations.length > 0 && (
          <IconButton
            className={styles.floatingAction}
            icon="clockBadge"
            label={automations.length === 1 ? automations[0].name : `${automations.length} automations`}
            onClick={(event) =>
              automations.length === 1 ? show({ kind: 'automation', automation: automations[0] }) : menu.open(event)
            }
          />
        )}
        {menu.anchor && <Menu anchor={menu.anchor} label="Automations" items={automationItems} onClose={menu.close} />}
      </header>
    )
  }
  return (
    <header className={styles.column}>
      <div className={styles.header}>
        <div className={styles.titleBlock}>
          <h1 className={styles.title}>{space.title}</h1>
          <span className={styles.agent} role="status" title={directory ?? undefined}>
            <span className={styles.dot} data-state={dot} />
            <span className={styles.agentName}>{status}</span>
            {directory && (
              <span className={styles.directory}>
                <bdi>{directory}</bdi>
              </span>
            )}
            {access && access !== 'workspace' && (
              <span className={styles.access} data-access={access}>
                {ACCESS_NAMES[access]}
              </span>
            )}
          </span>
        </div>
        <div className={styles.actions}>
          {automations.length > 0 && (
            <IconButton
              icon="clockBadge"
              label={automations.length === 1 ? automations[0].name : `${automations.length} automations`}
              onClick={() => show({ kind: 'automation', automation: automations[0] })}
            />
          )}
          <IconButton icon="more" label="Space options" onClick={(event) => menu.open(event)} />
        </div>
      </div>
      {menu.anchor && <Menu anchor={menu.anchor} label="Space options" items={items} onClose={menu.close} />}
    </header>
  )
}

/** Waits for the space: everything below needs its transcript element in place from the first render. */
export function Conversation({ spaceId, focusMessage }: { spaceId: string; focusMessage?: string }) {
  const space = useSpaces().find((item) => item.id === spaceId)
  return space ? <SpaceView space={space} focusMessage={focusMessage} /> : null
}

function SpaceView({ space, focusMessage }: { space: Space; focusMessage?: string }) {
  const { sync, transport } = useApp()
  const { show } = useNavigation()
  const connections = useConnections()
  const spaceId = space.id
  const connection = connections.find((item) => item.id === space.agent_connection_id)
  const keys = useTimeline(spaceId)
  const meta = useTimelineMeta(spaceId)
  const scroller = useRef<HTMLDivElement>(null)
  const content = useRef<HTMLDivElement>(null)
  const stack = useRef<HTMLDivElement>(null)
  const composer = useRef<ComposerHandle>(null)
  const { detached, onScroll, onWheel, attach, holdPosition, releasePosition } = useFollow(spaceId, keys, meta, {
    scroller,
    content,
    stack,
  })
  const spoken = useSpoken()
  const [findOpen, setFindOpen] = useState(false)
  const [found, setFound] = useState<string | null>(null)
  const [selecting, setSelecting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const lastPicked = useRef<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const dragDepth = useRef(0)

  useEffect(() => {
    sync.openSpace(spaceId)
    if (space?.unread && document.visibilityState === 'visible') sync.markRead(spaceId)
  }, [sync, spaceId, space.unread])

  const jump = () => {
    attach()
    composer.current?.focus()
  }

  useEffect(() => {
    if (focusMessage) void reveal(focusMessage)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusMessage, meta.loaded])

  const reveal = useCallback(
    async (messageId: string) => {
      if (!messages.timeline(spaceId).loaded) return
      for (let page = 0; page < 40; page++) {
        if (messages.keyFor(messageId)) break
        holdPosition()
        if (!(await messages.loadOlder(spaceId))) {
          releasePosition()
          break
        }
      }
      requestAnimationFrame(() => {
        const node = scroller.current?.querySelector(`[data-message="${messageId}"]`)
        node?.scrollIntoView({ block: 'center' })
        setFound(messageId)
      })
    },
    [spaceId, holdPosition, releasePosition],
  )

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'f') {
        event.preventDefault()
        setFindOpen(true)
      } else if (event.key === 'Escape' && selecting) {
        setSelecting(false)
        setSelected(new Set())
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selecting])

  const toggle = useCallback(
    (key: string, range: boolean) => {
      setSelected((current) => {
        const next = new Set(current)
        if (range && lastPicked.current) {
          const list = messages.timeline(spaceId).keys
          const [a, b] = [list.indexOf(lastPicked.current), list.indexOf(key)].sort((x, y) => x - y)
          for (const item of list.slice(a, b + 1)) next.add(item)
        } else if (next.has(key)) next.delete(key)
        else next.add(key)
        lastPicked.current = key
        return next
      })
    },
    [spaceId],
  )

  const startThread = useCallback((key: string) => {
    setSelecting(true)
    setSelected(new Set([key]))
    lastPicked.current = key
  }, [])

  // In the desktop app the OS drag arrives natively and in-page drag events do not fire, so a drop is
  // handled exactly once on either platform.
  useEffect(
    () =>
      transport.onFileDrop((files, phase) => {
        setDragging(phase === 'over')
        if (phase === 'drop' && files.length) composer.current?.addFiles(files)
      }),
    [transport],
  )

  const selection = useMemo(() => {
    const entries = keys.flatMap((key) => {
      const entry = selected.has(key) ? messages.entry(key) : undefined
      return entry && !entry.local ? [entry] : []
    })
    return entries
  }, [keys, selected])

  const empty = meta.loaded && keys.length === 0
  const onFiles = (event: React.DragEvent) => event.dataTransfer.types.includes('Files')

  return (
    <section
      className={styles.view}
      aria-label={space.title}
      onDragEnter={(event) => {
        if (!onFiles(event)) return
        event.preventDefault()
        dragDepth.current++
        setDragging(true)
      }}
      onDragOver={(event) => {
        if (onFiles(event)) event.preventDefault()
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1)
        if (!dragDepth.current) setDragging(false)
      }}
      onDrop={(event) => {
        if (!onFiles(event)) return
        event.preventDefault()
        dragDepth.current = 0
        setDragging(false)
        composer.current?.addFiles([...event.dataTransfer.files])
      }}
    >
      <Header space={space} connection={connection} onFind={() => setFindOpen(true)} />
      {findOpen && (
        <FindBar
          spaceId={spaceId}
          onReveal={(id) => void reveal(id)}
          onClose={() => {
            setFindOpen(false)
            setFound(null)
            composer.current?.focus()
          }}
        />
      )}
      <div
        ref={scroller}
        className={styles.scroller}
        tabIndex={-1}
        onScroll={onScroll}
        onWheel={onWheel}
        role="log"
        aria-label="Messages"
        aria-live="off"
      >
        <div ref={content} className={`${styles.column} ${styles.transcript}`}>
          <div ref={stack} className={styles.stack}>
            {meta.hasOlder && <div className={styles.loading}>Loading earlier messages</div>}
            {empty && <p className={styles.invitation}>What should we work on?</p>}
            {keys.map((key) => (
              <MessageRow
                key={key}
                id={key}
                highlighted={messages.entry(key)?.id === found}
                onThread={startThread}
                selection={{ active: selecting, selected: selected.has(key), onToggle: toggle }}
              />
            ))}
          </div>
        </div>
      </div>
      <div className={`${styles.column} ${styles.dock}`}>
        {detached && !selecting && (
          <button type="button" className={styles.jump} onClick={jump}>
            <Symbol name="down" size={14} />
            Jump to latest
          </button>
        )}
        {selecting ? (
          <div className={styles.selectBar} role="toolbar" aria-label="Create thread">
            <span className={styles.selectCount} aria-live="polite">
              {selection.length} {selection.length === 1 ? 'message' : 'messages'} selected
            </span>
            <Button
              onClick={() => {
                setSelecting(false)
                setSelected(new Set())
              }}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!selection.length}
              onClick={() => {
                show({ kind: 'thread', space, messageIds: selection.map((m) => m.id), preview: selection })
                setSelecting(false)
                setSelected(new Set())
              }}
            >
              Continue
            </Button>
          </div>
        ) : (
          <Composer
            ref={composer}
            spaceId={spaceId}
            placeholder={connection ? `Message ${connection.name}` : 'Message'}
            onSend={attach}
          />
        )}
      </div>
      {dragging && <div className={composerStyles.drop}>Drop files to attach</div>}
      <span className="sr" role="status">
        {spoken}
      </span>
    </section>
  )
}
