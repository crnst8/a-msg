import { createContext, useContext } from 'react'
import type { Automation, Message, Space } from '@/transport/operations'

export type Route =
  | { view: 'space'; id: string; focusMessage?: string }
  | { view: 'library'; artifactId?: string }
  | { view: 'code' }
  | { view: 'help' }
  | { view: 'settings'; tab?: 'appearance' | 'connections' | 'notifications' }

export type Overlay =
  | { kind: 'new-space' }
  | { kind: 'rename'; space: Space }
  | { kind: 'icon'; space: Space }
  | { kind: 'delete'; space: Space }
  | { kind: 'automation'; automation: Automation }
  | { kind: 'thread'; space: Space; messageIds: string[]; preview: Message[] }
  | { kind: 'palette' }
  | { kind: 'archive' }
  | { kind: 'new-session'; connectionId?: string }
  | { kind: 'new-connection' }
  | { kind: 'preview'; artifactId: string }
  | { kind: 'config'; connectionId: string }

export interface Navigation {
  route: Route
  navigate: (route: Route) => void
  overlay: Overlay | null
  show: (overlay: Overlay | null) => void
}

export const NavigationContext = createContext<Navigation | null>(null)

export function useNavigation(): Navigation {
  const value = useContext(NavigationContext)
  if (!value) throw new Error('NavigationContext is missing')
  return value
}
