import { useQuery } from '@tanstack/react-query'
import { createContext, useContext } from 'react'
import type { Automation, Connection, Space } from '@/transport/operations'
import type { AppTransport } from '@/transport/types'
import { keys, Sync } from './sync'

export interface AppContextValue {
  transport: AppTransport
  sync: Sync
}

export const AppContext = createContext<AppContextValue | null>(null)

export function useApp(): AppContextValue {
  const value = useContext(AppContext)
  if (!value) throw new Error('AppContext is missing')
  return value
}

export function useSpaces(): Space[] {
  const { transport } = useApp()
  const { data } = useQuery({
    queryKey: keys.spaces,
    queryFn: () => transport.call('listSpaces', undefined),
    staleTime: Infinity,
  })
  return data ?? EMPTY
}
const EMPTY: never[] = []

export function useConnections(): Connection[] {
  const { transport } = useApp()
  const { data } = useQuery({
    queryKey: keys.connections,
    queryFn: () => transport.call('listConnections', undefined),
    staleTime: Infinity,
  })
  return data ?? EMPTY
}

export function useAutomations(): Automation[] {
  const { transport } = useApp()
  const { data } = useQuery({
    queryKey: keys.automations,
    queryFn: () => transport.call('listAutomations', undefined),
    staleTime: Infinity,
  })
  return data ?? EMPTY
}
