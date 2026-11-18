import { useEffect, useSyncExternalStore } from 'react'
import { usePreferences, type RailPosition } from './preferences'

/** Phone widths. The rail sits at the bottom there, where a thumb reaches it, whatever the saved position. */
const PHONE = '(max-width: 600px)'

function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (listener) => {
      const list = window.matchMedia(query)
      list.addEventListener('change', listener)
      return () => list.removeEventListener('change', listener)
    },
    () => window.matchMedia(query).matches,
  )
}

export function usePhone(): boolean {
  return useMedia(PHONE)
}

/** The rail position in use: the saved one, or the bottom on a phone. */
export function useRail(): RailPosition {
  const { rail } = usePreferences()
  return usePhone() ? 'bottom' : rail
}

/** Touch screens without hover, where controls shown on hover appear for the tapped item instead. */
export const touch = typeof window !== 'undefined' && window.matchMedia('(hover: none)').matches

/**
 * Follows the on-screen keyboard. iOS keeps the layout viewport full height and lays the keyboard over it, so while
 * it is open the window takes the visible height (--app-height) and the rail steps aside (data-keyboard).
 */
export function useKeyboard(): void {
  useEffect(() => {
    const viewport = window.visualViewport
    if (!viewport || !touch) return
    const root = document.documentElement
    const update = () => {
      const editing = document.activeElement?.matches('input, textarea, [contenteditable=true]')
      const open = Boolean(editing && viewport.scale === 1 && window.innerHeight - viewport.height > 120)
      root.dataset.keyboard = String(open)
      if (open) {
        root.style.setProperty('--app-height', `${viewport.height}px`)
        root.style.setProperty('--app-top', `${viewport.offsetTop}px`)
      } else {
        root.style.removeProperty('--app-height')
        root.style.removeProperty('--app-top')
      }
    }
    update()
    viewport.addEventListener('resize', update)
    viewport.addEventListener('scroll', update)
    return () => {
      viewport.removeEventListener('resize', update)
      viewport.removeEventListener('scroll', update)
      delete root.dataset.keyboard
      root.style.removeProperty('--app-height')
      root.style.removeProperty('--app-top')
    }
  }, [])
}
