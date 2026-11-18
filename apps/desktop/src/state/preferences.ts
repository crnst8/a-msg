import { useSyncExternalStore } from 'react'
import type { PaletteName, ThemeTokens } from './theme'

export type RailPosition = 'left' | 'right' | 'top' | 'bottom'

export interface Preferences {
  version: 1
  rail: RailPosition
  expanded: boolean
  mode: 'system' | 'light' | 'dark'
  palette: PaletteName
  custom: { light?: Partial<ThemeTokens>; dark?: Partial<ThemeTokens> }
  font: 'haas' | 'system' | 'serif'
  textSize: number
  density: 'comfortable' | 'compact'
  enterSends: boolean
  notifyCompleted: boolean
  notifyFailures: boolean
  notifyReplies: boolean
  avatar: string | null
  showAgentName: boolean
}

export const DEFAULTS: Preferences = {
  version: 1,
  rail: 'left',
  expanded: false,
  mode: 'system',
  palette: 'paper',
  custom: {},
  font: 'haas',
  textSize: 16,
  density: 'comfortable',
  enterSends: true,
  notifyCompleted: true,
  notifyFailures: true,
  notifyReplies: true,
  avatar: null,
  showAgentName: false,
}

const KEY = 'amsg.preferences'
const listeners = new Set<() => void>()

function load(): Preferences {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return DEFAULTS
    const parsed = JSON.parse(raw)
    return parsed.version === 1 ? { ...DEFAULTS, ...parsed } : DEFAULTS
  } catch {
    return DEFAULTS
  }
}

let current = load()

export function preferences(): Preferences {
  return current
}

export function updatePreferences(patch: Partial<Preferences>): void {
  current = { ...current, ...patch }
  try {
    localStorage.setItem(KEY, JSON.stringify(current))
  } catch {
    /* Preferences still apply for this session. */
  }
  for (const listener of listeners) listener()
}

export function usePreferences(): Preferences {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    () => current,
  )
}
