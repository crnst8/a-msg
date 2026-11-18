import { memo, useState } from 'react'
import { useNavigation } from '@/app/navigation'
import { Logo } from '@/components/Logo'
import { Symbol } from '@/components/Symbol'
import type { SymbolName } from '@/components/symbols.gen'
import { Button, IconButton, Menu, useMenu } from '@/components/ui'
import { fileKind, fileSize, shortTime } from '@/state/format'
import { touch } from '@/state/layout'
import { messages, useEntry, type Entry } from '@/state/messages'
import { usePreferences } from '@/state/preferences'
import { useApp } from '@/state/queries'
import { reportError, toast } from '@/state/toasts'
import type { ArtifactRef } from '@/transport/operations'
import { Markdown } from './Markdown'
import styles from './Message.module.css'

export function fileSymbol(mime: string, name: string): SymbolName {
  if (mime.startsWith('image/')) return 'fileImage'
  if (mime.startsWith('audio/')) return 'fileAudio'
  if (mime.startsWith('video/')) return 'fileVideo'
  if (mime === 'application/pdf') return 'filePdf'
  if (mime === 'application/zip') return 'fileZip'
  if (/\.(sh|py|ts|js|rs|json|ya?ml|toml|css|html)$/i.test(name)) return 'fileCode'
  if (mime.startsWith('text/')) return 'fileText'
  return 'file'
}


/** Touch screens have no hover: a tap marks the message whose tools show, until another is tapped. */
function showTools(event: React.MouseEvent<HTMLElement>): void {
  if ((event.target as HTMLElement).closest('a, button, input, textarea')) return
  for (const row of document.querySelectorAll<HTMLElement>('[data-tapped]')) delete row.dataset.tapped
  event.currentTarget.dataset.tapped = 'true'
}

export function FileCard({ artifact }: { artifact: ArtifactRef }) {
  const { transport } = useApp()
  const { show } = useNavigation()
  if (artifact.state !== 'ready') {
    return (
      <div className={`${styles.file} ${styles.fileGone}`}>
        <span className={styles.fileIcon}>
          <Symbol name="file" size={24} />
        </span>
        <span className={styles.fileText}>
          <span className={styles.fileName}>{artifact.display_name}</span>
          <span className={styles.fileMeta}>Deleted</span>
        </span>
      </div>
    )
  }
  return (
    <div className={styles.file}>
      <span className={styles.fileIcon}>
        <Symbol name={fileSymbol(artifact.mime, artifact.display_name)} size={24} />
      </span>
      <span className={styles.fileText}>
        <button
          type="button"
          className={styles.fileName}
          onClick={() => show({ kind: 'preview', artifactId: artifact.id })}
        >
          {artifact.display_name}
        </button>
        <span className={styles.fileMeta}>
          {fileKind(artifact.mime, artifact.display_name)}, {fileSize(artifact.size)}
        </span>
      </span>
      <IconButton
        icon="download"
        label={`Save ${artifact.display_name}`}
        onClick={() => void transport.save(artifact).catch(reportError)}
      />
    </div>
  )
}

function Avatar({ author }: { author: string | null }) {
  const prefs = usePreferences()
  return (
    <span className={styles.avatar} aria-hidden="true">
      {prefs.avatar ? (
        <img src={prefs.avatar} alt="" />
      ) : author && /codex|claude|code/i.test(author) ? (
        <Symbol name="terminal" size={24} />
      ) : (
        <Logo className={styles.mark} size={28} />
      )}
    </span>
  )
}

function Status({ entry }: { entry: Entry }) {
  const { transport } = useApp()
  const run = entry.run_id
  if (entry.role === 'agent') {
    const retry = (
      <button
        type="button"
        className={styles.statusAction}
        onClick={() => run && void transport.call('retryRun', { id: run }).catch(reportError)}
      >
        Retry
      </button>
    )
    switch (entry.status) {
      case 'running':
        return (
          <div className={styles.status}>
            <span className={styles.pulse} />
            <span className={styles.activity} title={entry.activity ?? undefined}>
              {entry.activity ?? 'Working'}
            </span>
          </div>
        )
      case 'cancel_requested':
        return (
          <div className={styles.status}>
            <span className={styles.pulse} />
            <span>Stopping</span>
          </div>
        )
      case 'failed':
        return (
          <div className={`${styles.status} ${styles.statusError}`}>
            <Symbol name="alert" size={18} />
            <span>{entry.error_message ?? 'The run failed.'}</span>
            {retry}
          </div>
        )
      case 'cancelled':
        return (
          <div className={styles.status}>
            <span>Stopped</span>
            {retry}
          </div>
        )
      case 'delivery_uncertain':
        return (
          <div className={`${styles.status} ${styles.statusWarning}`}>
            <Symbol name="warning" size={18} />
            <span>{entry.error_message}</span>
            {retry}
          </div>
        )
      default:
        return null
    }
  }
  return null
}

function UserStatus({ entry, onEdit }: { entry: Entry; onEdit: () => void }) {
  const { transport, sync } = useApp()
  if (entry.local === 'send_failed') {
    return (
      <div className={`${styles.status} ${styles.statusUser} ${styles.statusError}`}>
        <Symbol name="alert" size={18} />
        <span>{entry.localError ?? 'Not sent'}</span>
        <button
          type="button"
          className={styles.statusAction}
          onClick={() => void transport.resend(entry.client_message_id!)}
        >
          Retry
        </button>
        <button
          type="button"
          className={styles.statusAction}
          onClick={() => {
            void transport.discard(entry.client_message_id!)
            messages.remove(entry.space_id, entry.key)
          }}
        >
          Remove
        </button>
      </div>
    )
  }
  if (entry.local) {
    return (
      <div className={`${styles.status} ${styles.statusUser}`}>
        <span>{entry.local === 'sending' ? 'Sending' : 'Queued'}</span>
      </div>
    )
  }
  if (entry.status === 'queued_server' || entry.status === 'accepted') {
    return (
      <div className={`${styles.status} ${styles.statusUser}`}>
        <span>Queued</span>
        <button type="button" className={styles.statusAction} onClick={onEdit}>
          Edit
        </button>
        <button
          type="button"
          className={styles.statusAction}
          onClick={() => void transport.call('deleteQueued', { id: entry.id }).catch(reportError)}
        >
          Remove
        </button>
      </div>
    )
  }
  void sync
  return null
}

function QueuedEditor({ entry, onDone }: { entry: Entry; onDone: () => void }) {
  const { transport } = useApp()
  const [text, setText] = useState(entry.markdown)
  return (
    <div>
      <textarea
        className={styles.editArea}
        value={text}
        aria-label="Edit queued message"
        autoFocus
        onChange={(event) => setText(event.target.value)}
      />
      <div className={styles.editActions}>
        <Button onClick={onDone}>Cancel</Button>
        <Button
          variant="primary"
          disabled={!text.trim()}
          onClick={() => {
            transport
              .call('patchQueued', { id: entry.id, body: { expected_revision: entry.revision, markdown: text } })
              .then(onDone)
              .catch((error) => {
                reportError(error)
                onDone()
              })
          }}
        >
          Save
        </Button>
      </div>
    </div>
  )
}

function Links({ entry }: { entry: Entry }) {
  const { navigate } = useNavigation()
  const links = entry.links.filter((link) => link.direction === 'continued_in')
  if (!links.length) return null
  return (
    <div className={styles.links}>
      {links.map((link) => (
        <button
          key={link.space_id}
          type="button"
          className={styles.link}
          disabled={!link.available}
          onClick={() => navigate({ view: 'space', id: link.space_id })}
        >
          <Symbol name="fork" size={16} />
          <span>
            Continued in <span className={styles.linkName}>{link.title}</span>
          </span>
        </button>
      ))}
    </div>
  )
}

function ContextCard({ entry }: { entry: Entry }) {
  const { navigate } = useNavigation()
  const [open, setOpen] = useState(false)
  const from = entry.links.find((link) => link.direction === 'from')
  const count = (entry.meta.selected_count as number) ?? 0
  const summary = entry.meta.generated_summary as string | null
  return (
    <div className={styles.context}>
      <div className={styles.contextHead}>
        <Symbol name="fork" size={16} />
        <span>
          {count} {count === 1 ? 'message' : 'messages'} from{' '}
          {from ? (
            <button
              type="button"
              className={styles.link}
              style={{ display: 'inline' }}
              disabled={!from.available}
              onClick={() => navigate({ view: 'space', id: from.space_id })}
            >
              <span className={styles.linkName}>{from.title}</span>
            </button>
          ) : (
            'another space'
          )}
        </span>
        <button
          type="button"
          className={styles.contextToggle}
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? 'Hide' : 'Show'}
        </button>
      </div>
      {open && (
        <div className={styles.contextBody}>
          <Markdown text={entry.markdown} />
        </div>
      )}
      {summary && (
        <div className={styles.summary}>
          <span className={styles.summaryLabel}>Generated summary</span>
          {summary}
        </div>
      )}
    </div>
  )
}

export interface SelectionProps {
  active: boolean
  selected: boolean
  onToggle: (key: string, range: boolean) => void
}

function Tools({ entry, onThread }: { entry: Entry; onThread: (key: string) => void }) {
  const menu = useMenu()
  return (
    <div className={styles.tools} data-open={Boolean(menu.anchor)}>
      <time className={styles.time} dateTime={entry.created_at}>
        {shortTime(entry.created_at)}
      </time>
      <IconButton
        className={styles.tool}
        icon="copy"
        size={16}
        label="Copy"
        onClick={() => {
          void navigator.clipboard.writeText(entry.markdown)
          toast('Copied')
        }}
      />
      <IconButton className={styles.tool} icon="more" size={16} label="More" onClick={(event) => menu.open(event)} />
      {menu.anchor && (
        <Menu
          anchor={menu.anchor}
          label="Message actions"
          onClose={menu.close}
          items={[{ label: 'Create thread from here', icon: 'fork', onSelect: () => onThread(entry.key) }]}
        />
      )}
    </div>
  )
}

export const MessageRow = memo(function MessageRow({
  id,
  selection,
  highlighted,
  onThread,
}: {
  id: string
  selection: SelectionProps
  highlighted: boolean
  onThread: (key: string) => void
}) {
  const entry = useEntry(id)
  const { showAgentName } = usePreferences()
  const [editing, setEditing] = useState(false)
  if (!entry || entry.role === 'event') return null
  const isUser = entry.role === 'user'
  const streaming = entry.status === 'running' || entry.status === 'cancel_requested'
  const pick = selection.active
    ? (event: React.MouseEvent) => {
        if (!(event.target as HTMLElement).closest('a, button')) selection.onToggle(entry.key, event.shiftKey)
      }
    : undefined
  return (
    <article
      className={`${styles.row} ${selection.active ? styles.selectable : ''}`}
      data-role={entry.role}
      data-message={entry.id}
      data-selected={selection.selected}
      data-find={highlighted}
      onClick={pick ?? (touch ? showTools : undefined)}
    >
      {selection.active && (
        <button
          type="button"
          role="checkbox"
          aria-checked={selection.selected}
          aria-label="Select message"
          className={styles.select}
          onClick={(event) => {
            event.stopPropagation()
            selection.onToggle(entry.key, event.shiftKey)
          }}
        >
          {selection.selected && <Symbol name="check" size={14} />}
        </button>
      )}
      {entry.role === 'context' ? (
        <ContextCard entry={entry} />
      ) : (
        <div
          className={`${styles.card} ${isUser ? styles.user : styles.agent} ${entry.local ? styles.pending : ''}`}
          aria-label={isUser ? 'You' : (entry.author ?? 'Agent')}
        >
          {!isUser && <Avatar author={entry.author} />}
          {!selection.active && !entry.local && !editing && <Tools entry={entry} onThread={onThread} />}
          {!isUser && (showAgentName || entry.attempt > 1) && (
            <span className={styles.author}>
              {showAgentName && (entry.author ?? 'Agent')}
              {entry.attempt > 1 && <span className={styles.attempt}>Attempt {entry.attempt}</span>}
            </span>
          )}
          {editing ? (
            <QueuedEditor entry={entry} onDone={() => setEditing(false)} />
          ) : (
            entry.markdown && <Markdown text={entry.markdown} streaming={streaming} />
          )}
          {entry.artifacts.length > 0 && (
            <div className={styles.files}>
              {entry.artifacts.map((artifact) => (
                <FileCard key={artifact.id} artifact={artifact} />
              ))}
            </div>
          )}
          {!isUser && <Status entry={entry} />}
          {isUser && !editing && <UserStatus entry={entry} onEdit={() => setEditing(true)} />}
        </div>
      )}
      <Links entry={entry} />
    </article>
  )
})
