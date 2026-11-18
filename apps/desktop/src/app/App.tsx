import { useCallback, useEffect, useMemo, useState } from 'react'
import { Toasts } from '@/components/ui'
import { AutomationSheet } from '@/features/automation/AutomationSheet'
import { CodeConnector } from '@/features/code/CodeConnector'
import { ConfigSheet, NewConnectionSheet, NewSessionSheet } from '@/features/code/sheets'
import { Conversation } from '@/features/conversation/Conversation'
import { Library } from '@/features/library/Library'
import { Help } from '@/features/help/Help'
import { PreviewSheet } from '@/features/library/Preview'
import { Onboarding } from '@/features/onboarding/Onboarding'
import { Palette } from '@/features/palette/Palette'
import { Rail } from '@/features/rail/Rail'
import { Settings } from '@/features/settings/Settings'
import { ArchiveSheet, DeleteSheet, IconSheet, NewSpaceSheet, RenameSheet } from '@/features/spaces/sheets'
import { ThreadSheet } from '@/features/spaces/ThreadSheet'
import { useKeyboard, useRail } from '@/state/layout'
import { usePreferences } from '@/state/preferences'
import { useApp, useSpaces } from '@/state/queries'
import { useLink } from '@/state/sync'
import { toast } from '@/state/toasts'
import styles from './App.module.css'
import { NavigationContext, type Navigation, type Overlay, type Route } from './navigation'
import { dismissSplash } from './splash'

const ROUTE_KEY = 'amsg.route'

function savedRoute(): Route | null {
  const space = new URLSearchParams(location.search).get('space')
  if (space && /^[a-zA-Z0-9-]{1,64}$/.test(space)) {
    history.replaceState(null, '', location.pathname)
    return { view: 'space', id: space }
  }
  try {
    return JSON.parse(localStorage.getItem(ROUTE_KEY) ?? 'null')
  } catch {
    return null
  }
}

function clearRoute(): void {
  try {
    localStorage.removeItem(ROUTE_KEY)
  } catch {
    /* A stale route falls back to General. */
  }
}

export function App() {
  const { sync } = useApp()
  const link = useLink()
  useEffect(() => {
    void sync.boot()
  }, [sync])
  const connected = useCallback(() => {
    clearRoute()
    sync.connected()
  }, [sync])
  useEffect(() => {
    if (link.paired !== null) dismissSplash()
  }, [link.paired])
  if (link.paired === null) return null
  return (
    <>
      {!link.paired || link.unauthorized ? <Onboarding onPaired={connected} revoked={link.unauthorized} /> : <Workspace />}
      <Toasts />
    </>
  )
}

function Workspace() {
  const { sync, transport } = useApp()
  // Window controls exist only in the desktop app; the dev harness stands in for it. iOS user agents say
  // "like Mac OS X" but never "Macintosh".
  const platform = transport.kind !== 'web' && navigator.userAgent.includes('Macintosh') ? 'mac' : 'other'
  const prefs = usePreferences()
  const rail = useRail()
  useKeyboard()
  const spaces = useSpaces()
  const general = spaces.find((space) => space.kind === 'general')
  const [route, setRoute] = useState<Route | null>(savedRoute)
  const [overlay, setOverlay] = useState<Overlay | null>(null)

  useEffect(() => {
    void sync.start()
  }, [sync])

  const navigate = useCallback((next: Route) => {
    setRoute(next)
    const { focusMessage: _, ...saved } = next as Route & { focusMessage?: string }
    try {
      localStorage.setItem(ROUTE_KEY, JSON.stringify(saved))
    } catch {
      /* The route resets to General on the next launch. */
    }
  }, [])

  useEffect(
    () =>
      sync.subscribe((event) => {
        if (event.type === 'space_created_by_agent') {
          // Agent and automation actions never take focus: they offer a link.
          toast(`New space: ${event.space.title}`, {
            action: { label: 'Open', run: () => navigate({ view: 'space', id: event.space.id }) },
          })
        } else if (event.type === 'space_deleted' && route?.view === 'space' && route.id === event.id && general) {
          navigate({ view: 'space', id: general.id })
        }
      }),
    [sync, navigate, route, general],
  )

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const command = event.metaKey || event.ctrlKey
      if (!command) return
      if (event.key === 'k') {
        event.preventDefault()
        setOverlay((current) => (current?.kind === 'palette' ? null : { kind: 'palette' }))
      } else if (event.key === ',') {
        event.preventDefault()
        navigate({ view: 'settings' })
      } else if (event.key === 'n' && !event.shiftKey) {
        event.preventDefault()
        setOverlay({ kind: 'new-space' })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])

  const current: Route | null = useMemo(() => {
    if (route?.view === 'space' && spaces.length && !spaces.some((space) => space.id === route.id)) {
      return general ? { view: 'space', id: general.id } : null
    }
    return route ?? (general ? { view: 'space', id: general.id } : null)
  }, [route, spaces, general])

  const navigation: Navigation = useMemo(
    () => ({ route: current ?? { view: 'library' }, navigate, overlay, show: setOverlay }),
    [current, navigate, overlay],
  )

  useEffect(() => {
    sync.currentSpace = current?.view === 'space' ? current.id : null
  }, [sync, current])

  useEffect(() => {
    const open = (event: Event) => navigate({ view: 'space', id: (event as CustomEvent<string>).detail })
    window.addEventListener('amsg:open-space', open)
    return () => window.removeEventListener('amsg:open-space', open)
  }, [navigate])

  return (
    <NavigationContext.Provider value={navigation}>
      <div
        className={styles.window}
        data-rail={rail}
        data-platform={platform}
        style={
          {
            '--rail-width': `${prefs.expanded && (rail === 'left' || rail === 'right') ? 220 : 72}px`,
          } as React.CSSProperties
        }
      >
        <div className={styles.drag} data-tauri-drag-region />
        <Rail />
        <main className={styles.sheet}>
          {current?.view === 'space' && (
            <Conversation key={current.id} spaceId={current.id} focusMessage={current.focusMessage} />
          )}
          {current?.view === 'library' && <Library initial={current.artifactId} />}
          {current?.view === 'code' && <CodeConnector />}
          {current?.view === 'settings' && <Settings initialTab={current.tab} />}
          {current?.view === 'help' && <Help />}
        </main>
      </div>
      {overlay?.kind === 'new-space' && <NewSpaceSheet onClose={() => setOverlay(null)} />}
      {overlay?.kind === 'rename' && <RenameSheet space={overlay.space} onClose={() => setOverlay(null)} />}
      {overlay?.kind === 'icon' && <IconSheet space={overlay.space} onClose={() => setOverlay(null)} />}
      {overlay?.kind === 'delete' && <DeleteSheet space={overlay.space} onClose={() => setOverlay(null)} />}
      {overlay?.kind === 'archive' && <ArchiveSheet onClose={() => setOverlay(null)} />}
      {overlay?.kind === 'automation' && (
        <AutomationSheet automationId={overlay.automation.id} onClose={() => setOverlay(null)} />
      )}
      {overlay?.kind === 'thread' && <ThreadSheet {...overlay} onClose={() => setOverlay(null)} />}
      {overlay?.kind === 'palette' && <Palette onClose={() => setOverlay(null)} />}
      {overlay?.kind === 'new-session' && (
        <NewSessionSheet connectionId={overlay.connectionId} onClose={() => setOverlay(null)} />
      )}
      {overlay?.kind === 'new-connection' && <NewConnectionSheet onClose={() => setOverlay(null)} />}
      {overlay?.kind === 'config' && <ConfigSheet connectionId={overlay.connectionId} onClose={() => setOverlay(null)} />}
      {overlay?.kind === 'preview' && <PreviewSheet artifactId={overlay.artifactId} onClose={() => setOverlay(null)} />}
    </NavigationContext.Provider>
  )
}
