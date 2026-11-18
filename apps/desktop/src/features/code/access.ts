import type { Connection, Space } from '@/transport/operations'

export type Access = 'read' | 'workspace' | 'full'

export const ACCESS_LEVELS: Access[] = ['read', 'workspace', 'full']
export const ACCESS_NAMES: Record<Access, string> = { read: 'Read only', workspace: 'Workspace', full: 'Full access' }

const CODEX: Record<string, Access> = { 'read-only': 'read', 'workspace-write': 'workspace', 'danger-full-access': 'full' }
const CLAUDE: Record<string, Access> = { plan: 'read', acceptEdits: 'workspace', bypassPermissions: 'full' }

const isAccess = (value: unknown): value is Access => ACCESS_LEVELS.includes(value as Access)

/** The levels a session on this connection may choose, and the one it gets by default. Null when it has no choice. */
export function accessOptions(connection: Connection | undefined): { levels: Access[]; fallback: Access } | null {
  if (!connection) return null
  const capabilities = connection.capabilities ?? {}
  let top: unknown = capabilities.max_access
  let fallback: unknown = capabilities.default_access
  if (!isAccess(top)) {
    // A runner that does not report a ceiling cannot honour a choice. A connection on the service's host can.
    if (connection.kind !== 'codex' && connection.kind !== 'claude') return null
    const config = connection.config ?? {}
    fallback =
      connection.kind === 'codex'
        ? (CODEX[(config.sandbox as string) ?? 'workspace-write'] ?? 'workspace')
        : (CLAUDE[(config.permission_mode as string) ?? 'acceptEdits'] ?? 'workspace')
    top = isAccess(config.max_access) ? config.max_access : 'full'
  }
  const safeFallback: Access = isAccess(fallback) ? fallback : 'workspace'
  const ceiling = ACCESS_LEVELS.indexOf(top as Access)
  const levels = ACCESS_LEVELS.filter((_, index) => index <= Math.max(ceiling, ACCESS_LEVELS.indexOf(safeFallback)))
  return levels.length > 1 ? { levels, fallback: safeFallback } : null
}

export function sessionAccess(space: Space, connection: Connection | undefined): Access | null {
  const options = accessOptions(connection)
  const chosen = space.session?.access
  if (isAccess(chosen)) return chosen
  return options?.fallback ?? null
}
