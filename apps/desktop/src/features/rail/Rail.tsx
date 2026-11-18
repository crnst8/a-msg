import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useNavigation, type Route } from '@/app/navigation'
import { SpaceIcon, Symbol } from '@/components/Symbol'
import { Menu, Tooltip, useMenu, type MenuEntry } from '@/components/ui'
import { zonedTime } from '@/state/format'
import { usePhone, useRail } from '@/state/layout'
import { updatePreferences, usePreferences, type RailPosition } from '@/state/preferences'
import { useApp, useAutomations, useSpaces } from '@/state/queries'
import { reportError, toast } from '@/state/toasts'
import type { Automation, Space } from '@/transport/operations'
import styles from './Rail.module.css'

const asset = (name: string) => `/assets/navigation/${name}.png`

function isCurrent(route: Route, target: Route): boolean {
  if (route.view !== target.view) return false
  return route.view !== 'space' || route.id === (target as { id: string }).id
}

export function useMoveSpace() {
  const { transport } = useApp()
  const spaces = useSpaces()
  return (space: Space, index: number) => {
    const threads = spaces.filter((item) => item.kind === 'thread')
    const from = threads.findIndex((item) => item.id === space.id)
    if (index === from || index < 0 || index >= threads.length) return
    transport
      .call('patchSpace', { id: space.id, body: { expected_revision: space.revision, ordinal: index } })
      .then((updated) => {
        toast(`Moved ${space.title}`, {
          action: {
            label: 'Undo',
            run: () =>
              void transport
                .call('patchSpace', { id: space.id, body: { expected_revision: updated.revision, ordinal: from } })
                .catch(reportError),
          },
        })
      })
      .catch(reportError)
  }
}

export function useArchiveSpace() {
  const { transport } = useApp()
  const { route, navigate } = useNavigation()
  const spaces = useSpaces()
  return (space: Space) => {
    transport
      .call('archiveSpace', { id: space.id })
      .then(() => {
        if (route.view === 'space' && route.id === space.id) {
          const general = spaces.find((item) => item.kind === 'general')
          if (general) navigate({ view: 'space', id: general.id })
        }
        toast(`Archived ${space.title}`, {
          action: {
            label: 'Undo',
            run: () => void transport.call('restoreSpace', { id: space.id }).catch(reportError),
          },
        })
      })
      .catch(reportError)
  }
}

function automationDetail(automations: Automation[]): string | undefined {
  if (!automations.length) return undefined
  if (automations.length > 1) return `${automations.length} automations`
  const [job] = automations
  if (job.state === 'paused') return 'Automation paused'
  return `Next ${zonedTime(job.next_run_at, job.timezone)}`
}

function tipSide(position: RailPosition) {
  return ({ left: 'right', right: 'left', top: 'bottom', bottom: 'top' } as const)[position]
}

export function Rail() {
  const prefs = usePreferences()
  const rail = useRail()
  const phone = usePhone()
  const { route, navigate, show } = useNavigation()
  const spaces = useSpaces()
  const automations = useAutomations()
  const move = useMoveSpace()
  const archive = useArchiveSpace()
  const horizontal = rail === 'top' || rail === 'bottom'
  const expanded = prefs.expanded && !horizontal
  const general = spaces.find((space) => space.kind === 'general')
  const threads = useMemo(() => spaces.filter((space) => space.kind === 'thread'), [spaces])
  const scrollRef = useRef<HTMLDivElement>(null)
  const groupRef = useRef<HTMLDivElement>(null)
  const railMenu = useMenu()
  const spaceMenu = useMenu()
  const moreMenu = useMenu()
  const [menuSpace, setMenuSpace] = useState<Space | null>(null)
  const [visible, setVisible] = useState(Number.MAX_SAFE_INTEGER)
  const [drag, setDrag] = useState<{ id: string; index: number } | null>(null)

  useLayoutEffect(() => {
    const group = groupRef.current
    if (!horizontal || !group) return setVisible(Number.MAX_SAFE_INTEGER)
    const measure = () => {
      const style = getComputedStyle(group)
      const room = group.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) - 44
      const itemSize = phone ? 44 : 52
      const fits = Math.floor(room / itemSize)
      setVisible(fits >= threads.length ? threads.length : Math.max(0, Math.floor((room - 36) / itemSize)))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(group)
    return () => observer.disconnect()
  }, [horizontal, phone, threads.length])

  useEffect(() => {
    if (route.view === 'space' && !horizontal) {
      scrollRef.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: 'nearest' })
    }
  }, [route, horizontal])

  const linked = (id: string) => automations.filter((job) => job.space_id === id)
  const shown = threads.slice(0, visible)
  const hidden = threads.slice(visible)

  const positions: { value: RailPosition; label: string }[] = [
    { value: 'left', label: 'Left' },
    { value: 'right', label: 'Right' },
    { value: 'top', label: 'Top' },
    { value: 'bottom', label: 'Bottom' },
  ]
  const railItems: MenuEntry[] = [
    ...(horizontal
      ? []
      : [
          {
            label: 'Show labels',
            checked: prefs.expanded,
            onSelect: () => updatePreferences({ expanded: !prefs.expanded }),
          },
          { rule: true },
        ]),
    ...(phone ? [] : positions).map((position) => ({
      label: position.label,
      checked: prefs.rail === position.value,
      onSelect: () => updatePreferences({ rail: position.value }),
    })),
  ]

  const spaceItems = (space: Space): MenuEntry[] => {
    const index = threads.findIndex((item) => item.id === space.id)
    return [
      { label: 'Rename', icon: 'rename', onSelect: () => show({ kind: 'rename', space }) },
      { label: 'Change icon', icon: 'star', onSelect: () => show({ kind: 'icon', space }) },
      { rule: true },
      { label: 'Move up', icon: 'moveUp', disabled: index <= 0, onSelect: () => move(space, index - 1) },
      {
        label: 'Move down',
        icon: 'moveDown',
        disabled: index >= threads.length - 1,
        onSelect: () => move(space, index + 1),
      },
      { rule: true },
      { label: 'Archive', icon: 'archive', onSelect: () => archive(space) },
      { label: 'Delete', icon: 'trash', danger: true, onSelect: () => show({ kind: 'delete', space }) },
    ]
  }

  const startDrag = (event: React.PointerEvent<HTMLElement>, space: Space) => {
    if (event.button !== 0) return
    const start = horizontal ? event.clientX : event.clientY
    const target = event.currentTarget
    const pointer = event.pointerId
    let active = false
    const onMove = (moveEvent: PointerEvent) => {
      const at = horizontal ? moveEvent.clientX : moveEvent.clientY
      if (!active && Math.abs(at - start) < 6) return
      if (!active) {
        active = true
        target.setPointerCapture(pointer)
      }
      const nodes = [...scrollRef.current!.querySelectorAll<HTMLElement>('[data-space]')]
      let index = nodes.length
      for (let i = 0; i < nodes.length; i++) {
        const rect = nodes[i].getBoundingClientRect()
        const middle = horizontal ? rect.left + rect.width / 2 : rect.top + rect.height / 2
        if (at < middle) {
          index = i
          break
        }
      }
      setDrag({ id: space.id, index })
    }
    const finish = () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', finish)
      window.removeEventListener('pointercancel', finish)
      if (!active) return
      setDrag((current) => {
        if (current) {
          const from = threads.findIndex((item) => item.id === space.id)
          queueMicrotask(() => move(space, current.index > from ? current.index - 1 : current.index))
        }
        return null
      })
      // The click that ends a drag must not navigate.
      target.addEventListener('click', (click) => click.stopPropagation(), { capture: true, once: true })
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', finish)
    window.addEventListener('pointercancel', finish)
  }

  const dropLine = () => {
    if (!drag || !scrollRef.current) return null
    const nodes = [...scrollRef.current.querySelectorAll<HTMLElement>('[data-space]')]
    const box = scrollRef.current.getBoundingClientRect()
    const reference = nodes[drag.index] ?? nodes[nodes.length - 1]
    if (!reference) return null
    const rect = reference.getBoundingClientRect()
    const atEnd = drag.index >= nodes.length
    return (
      <div
        className={styles.dropLine}
        style={
          horizontal
            ? { left: (atEnd ? rect.right : rect.left) - 1, top: box.top + 12, width: 2, height: box.height - 24 }
            : { top: (atEnd ? rect.bottom : rect.top) - 1, left: box.left + 12, height: 2, width: box.width - 24 }
        }
      />
    )
  }

  const common = { expanded, position: rail }

  return (
    <nav
      className={styles.rail}
      data-position={rail}
      aria-label="Navigation"
      data-tauri-drag-region="deep"
      onContextMenu={(event) => {
        event.preventDefault()
        railMenu.openAt(event.clientX, event.clientY)
      }}
    >
      <div className={styles.primary}>
        {general && (
          <RailItem
            {...common}
            label="General"
            image="general"
            unread={general.unread}
            current={isCurrent(route, { view: 'space', id: general.id })}
            onClick={() => navigate({ view: 'space', id: general.id })}
          />
        )}
        <RailItem
          {...common}
          label="Library"
          image="library"
          current={route.view === 'library'}
          onClick={() => navigate({ view: 'library' })}
        />
        <RailItem
          {...common}
          label="Code Connector"
          image="code"
          current={
            route.view === 'code' ||
            (route.view === 'space' && spaces.find((space) => space.id === route.id)?.kind === 'code')
          }
          onClick={() => navigate({ view: 'code' })}
        />
      </div>

      <div className={styles.group} ref={groupRef}>
        <div className={styles.scroll} ref={scrollRef} role="list" aria-label="Automated and threads">
          {shown.map((space) => {
            const jobs = linked(space.id)
            return (
              <div role="listitem" key={space.id} style={{ display: 'contents' }}>
                <RailItem
                  {...common}
                  label={space.title}
                  detail={automationDetail(jobs)}
                  icon={<SpaceIcon icon={space.icon} size={26} />}
                  unread={space.unread}
                  scheduled={jobs.length > 0}
                  spaceId={space.id}
                  dragging={drag?.id === space.id}
                  current={isCurrent(route, { view: 'space', id: space.id })}
                  onClick={() => navigate({ view: 'space', id: space.id })}
                  onPointerDown={(event) => startDrag(event, space)}
                  onContextMenu={(event) => {
                    event.preventDefault()
                    event.stopPropagation()
                    setMenuSpace(space)
                    spaceMenu.openAt(event.clientX, event.clientY, event.currentTarget)
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'ContextMenu' || (event.key === 'F10' && event.shiftKey)) {
                      event.preventDefault()
                      const rect = event.currentTarget.getBoundingClientRect()
                      setMenuSpace(space)
                      spaceMenu.openAt(rect.right, rect.top, event.currentTarget)
                    }
                  }}
                />
              </div>
            )
          })}
        </div>
        {hidden.length > 0 && (
          <Tooltip text={`${hidden.length} more`}>
            <button
              type="button"
              className={styles.more}
              aria-label={`${hidden.length} more spaces`}
              onClick={(event) => moreMenu.open(event, 'left')}
            >
              <Symbol name="caret" size={16} />
            </button>
          </Tooltip>
        )}
        <Tooltip text="New space" side={tipSide(rail)}>
          <button
            type="button"
            className={styles.new}
            aria-label="New space"
            onClick={() => show({ kind: 'new-space' })}
          >
            <span className={styles.newTile}>
              <Symbol name="plus" size={14} />
            </span>
            {expanded && <span className={styles.label}>New space</span>}
          </button>
        </Tooltip>
      </div>

      <div className={styles.settings}>
        <RailItem
          {...common}
          label="Help"
          icon={<span className={styles.helpIcon} aria-hidden="true">?</span>}
          current={route.view === 'help'}
          onClick={() => navigate({ view: 'help' })}
        />
        <RailItem
          {...common}
          label="Settings"
          image="settings"
          current={route.view === 'settings'}
          onClick={() => navigate({ view: 'settings' })}
        />
      </div>
      {dropLine()}
      {railMenu.anchor && (
        <Menu anchor={railMenu.anchor} items={railItems} label="Navigation options" onClose={railMenu.close} />
      )}
      {spaceMenu.anchor && menuSpace && (
        <Menu anchor={spaceMenu.anchor} items={spaceItems(menuSpace)} label={menuSpace.title} onClose={spaceMenu.close} />
      )}
      {moreMenu.anchor && (
        <Menu
          anchor={moreMenu.anchor}
          label="More spaces"
          items={hidden.map((space) => ({
            label: space.title,
            onSelect: () => navigate({ view: 'space', id: space.id }),
          }))}
          onClose={moreMenu.close}
        />
      )}
    </nav>
  )
}

function RailItem({
  label,
  detail,
  image,
  icon,
  current,
  expanded,
  position,
  unread,
  scheduled,
  spaceId,
  dragging,
  ...handlers
}: {
  label: string
  detail?: string
  image?: string
  icon?: React.ReactNode
  current: boolean
  expanded: boolean
  position: RailPosition
  unread?: boolean
  scheduled?: boolean
  spaceId?: string
  dragging?: boolean
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const name = [label, unread ? 'unread' : '', detail ?? (scheduled ? 'automation' : '')].filter(Boolean).join(', ')
  return (
    <Tooltip text={label} detail={detail} side={tipSide(position)}>
      <button
        type="button"
        className={styles.item}
        aria-label={name}
        aria-current={current ? 'page' : undefined}
        data-space={spaceId}
        data-dragging={dragging}
        {...handlers}
      >
        <span className={styles.glyph}>
          {image ? <img className={styles.image} src={asset(image)} alt="" draggable={false} /> : icon}
          {unread && <span className={styles.unread} />}
          {scheduled && (
            <span className={styles.clock}>
              <Symbol name="clockBadge" size={12} />
            </span>
          )}
        </span>
        {expanded && <span className={styles.label}>{label}</span>}
      </button>
    </Tooltip>
  )
}
