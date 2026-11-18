import { useState } from 'react'
import { useNavigation } from '@/app/navigation'
import { SpaceIcon, Symbol } from '@/components/Symbol'
import { Button, IconButton, Menu, useMenu } from '@/components/ui'
import { connectionState, fileDate } from '@/state/format'
import { useApp, useConnections, useSpaces } from '@/state/queries'
import { reportError } from '@/state/toasts'
import type { Connection, ConnectionTest } from '@/transport/operations'
import styles from './Code.module.css'

export const KIND_NAMES: Record<string, string> = {
  codex: 'Codex',
  claude: 'Claude Code',
  bridge: 'Bridge',
  hermes: 'Hermes',
  fake: 'Development only',
}

function ConnectionCard({ connection }: { connection: Connection }) {
  const { transport } = useApp()
  const { navigate, show } = useNavigation()
  const sessions = useSpaces().filter((space) => space.kind === 'code' && space.agent_connection_id === connection.id)
  const [test, setTest] = useState<ConnectionTest | null>(null)
  const [testing, setTesting] = useState(false)
  const menu = useMenu()
  const agent = (connection.capabilities.agent as string | undefined) ?? connection.kind
  const kind = KIND_NAMES[agent] ?? agent
  const run = () => {
    setTesting(true)
    transport
      .call('testConnection', { id: connection.id })
      .then(setTest)
      .catch(reportError)
      .finally(() => setTesting(false))
  }
  return (
    <section className={styles.card} aria-label={connection.name}>
      <div className={styles.cardHead}>
        <div className={styles.cardTitle}>
          <span className={styles.name}>{connection.name}</span>
          <span className={styles.kind}>
            <span className={styles.dot} data-state={connection.state} />
            {kind !== connection.name ? `${kind}, ${connectionState(connection.state)}` : connectionState(connection.state)}
          </span>
        </div>
        <Button variant="outline" onClick={() => show({ kind: 'new-session', connectionId: connection.id })}>
          New session
        </Button>
        <IconButton icon="more" label={`${connection.name} options`} onClick={(event) => menu.open(event)} />
        {menu.anchor && (
          <Menu
            anchor={menu.anchor}
            label={`${connection.name} options`}
            onClose={menu.close}
            items={[
              { label: testing ? 'Testing' : 'Test connection', icon: 'connection', disabled: testing, onSelect: run },
              ...(connection.config?.config_path
                ? [{ label: 'Edit configuration', icon: 'rename' as const, onSelect: () => show({ kind: 'config', connectionId: connection.id }) }]
                : []),
            ]}
          />
        )}
      </div>
      {test && (
        <ul className={styles.checks}>
          {test.checks.map((check, index) => (
            <li key={index} className={`${styles.check} ${check.ok ? styles.checkOk : styles.checkBad}`}>
              <Symbol name={check.ok ? 'success' : 'alert'} size={18} />
              <span>{check.detail as string}</span>
            </li>
          ))}
        </ul>
      )}
      {sessions.length > 0 && (
        <div className={styles.sessions}>
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              className={styles.session}
              onClick={() => navigate({ view: 'space', id: session.id })}
            >
              <span className={styles.sessionIcon}>
                <SpaceIcon icon={session.icon} size={20} />
              </span>
              <span className={styles.sessionText}>
                <span className={styles.sessionTitle}>{session.title}</span>
                <span className={styles.sessionPath}>{session.session?.working_directory}</span>
              </span>
              <span className={styles.sessionDate}>{fileDate(session.updated_at)}</span>
            </button>
          ))}
        </div>
      )}
    </section>
  )
}

export function CodeConnector() {
  const { show } = useNavigation()
  const connections = useConnections().filter((connection) => connection.kind !== 'hermes' && connection.kind !== 'fake')
  return (
    <div className={styles.view}>
      <div className={styles.column}>
        <div className={styles.heading}>
          <h1 className={styles.title}>Code Connector</h1>
          {connections.length > 0 && (
            <Button variant="outline" onClick={() => show({ kind: 'new-connection' })}>
              Add connection
            </Button>
          )}
        </div>
        {connections.length === 0 ? (
          <div className={styles.empty}>
            <Button variant="primary" onClick={() => show({ kind: 'new-connection' })}>
              Add connection
            </Button>
          </div>
        ) : (
          <div className={styles.cards}>
            {connections.map((connection) => (
              <ConnectionCard key={connection.id} connection={connection} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
