import { useState } from 'react'
import { useNavigation } from '@/app/navigation'
import { Button, Field, Sheet, inputClass, selectClass } from '@/components/ui'
import { useApp, useConnections } from '@/state/queries'
import { reportError } from '@/state/toasts'
import type { Icon, Message, Space } from '@/transport/operations'
import { IconPicker } from './sheets'
import styles from './sheets.module.css'

const LIMIT = 60

function suggest(preview: Message[]): string {
  const first = preview.find((message) => message.role === 'user' && message.markdown.trim()) ?? preview[0]
  const line = first.markdown.replace(/[#>*_`]/g, '').replace(/\s+/g, ' ').trim()
  return line.length <= LIMIT ? line : `${line.slice(0, LIMIT - 1).trimEnd()}…`
}

export function ThreadSheet({
  space,
  messageIds,
  preview,
  onClose,
}: {
  space: Space
  messageIds: string[]
  preview: Message[]
  onClose: () => void
}) {
  const { transport, sync } = useApp()
  const { navigate } = useNavigation()
  const connections = useConnections().filter((connection) => connection.kind !== 'codex' && connection.kind !== 'claude')
  const [title, setTitle] = useState(() => suggest(preview))
  const [icon, setIcon] = useState<Icon>({ kind: 'catalog', value: 'thread' })
  const [connection, setConnection] = useState(space.agent_connection_id ?? '')
  const [key] = useState(() => crypto.randomUUID())
  const [busy, setBusy] = useState(false)
  const create = () => {
    if (busy) return
    setBusy(true)
    sync.expectSpace()
    transport
      .call('fork', {
        id: space.id,
        body: {
          source_message_ids: messageIds,
          title: title.trim() || null,
          icon,
          agent_connection_id: connection || null,
          idempotency_key: key,
        },
      })
      .then((result) => {
        onClose()
        navigate({ view: 'space', id: result.space.id })
      })
      .catch((error) => {
        setBusy(false)
        reportError(error)
      })
  }
  return (
    <Sheet
      title="Create thread"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={busy} onClick={create}>
            Create
          </Button>
        </>
      }
    >
      <div className={styles.stack}>
        <div className={styles.preview} tabIndex={0} aria-label="Included messages">
          {preview.map((message) => (
            <div key={message.id} className={styles.previewItem}>
              <span className={styles.previewWho}>{message.role === 'user' ? 'You' : (message.author ?? 'Agent')}</span>
              <span className={styles.previewText}>{message.markdown.replace(/[#*`>_]/g, '')}</span>
            </div>
          ))}
        </div>
        <Field label="Name">
          <input className={inputClass} value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} />
        </Field>
        {connections.length > 1 && (
          <Field label="Agent">
            <select className={selectClass} value={connection} onChange={(event) => setConnection(event.target.value)}>
              {connections.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        <IconPicker value={icon} onChange={setIcon} />
      </div>
    </Sheet>
  )
}
