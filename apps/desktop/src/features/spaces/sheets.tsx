import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigation } from '@/app/navigation'
import { SpaceIcon, Symbol } from '@/components/Symbol'
import { CATALOG } from '@/components/symbols.gen'
import { Button, Field, IconButton, Sheet, inputClass } from '@/components/ui'
import { useArchiveSpace } from '@/features/rail/Rail'
import { useApp, useAutomations } from '@/state/queries'
import { keys } from '@/state/sync'
import { reportError, toast } from '@/state/toasts'
import type { Icon, Operation, Space } from '@/transport/operations'
import styles from './sheets.module.css'

export function IconPicker({ value, onChange }: { value: Icon; onChange: (icon: Icon) => void }) {
  return (
    <div className={styles.stack}>
      <div className={styles.icons} role="radiogroup" aria-label="Icon">
        {CATALOG.map((name) => (
          <button
            key={name}
            type="button"
            role="radio"
            aria-label={name}
            aria-checked={value.kind === 'catalog' && value.value === name}
            className={styles.icon}
            onClick={() => onChange({ kind: 'catalog', value: name })}
          >
            <Symbol name={name} size={22} />
          </button>
        ))}
      </div>
      <div className={styles.emojiRow}>
        <input
          className={`${inputClass} ${styles.emoji}`}
          aria-label="Emoji"
          placeholder="Emoji"
          maxLength={8}
          value={value.kind === 'emoji' ? value.value : ''}
          onChange={(event) => {
            const text = event.target.value.trim()
            onChange(text ? { kind: 'emoji', value: text } : { kind: 'catalog', value: 'thread' })
          }}
        />
      </div>
    </div>
  )
}

export function NewSpaceSheet({ onClose }: { onClose: () => void }) {
  const { transport, sync } = useApp()
  const { navigate } = useNavigation()
  const [title, setTitle] = useState('')
  const [icon, setIcon] = useState<Icon>({ kind: 'catalog', value: 'thread' })
  const [busy, setBusy] = useState(false)
  const create = () => {
    if (!title.trim() || busy) return
    setBusy(true)
    sync.expectSpace()
    transport
      .call('createSpace', { body: { kind: 'thread', title: title.trim(), icon, idempotency_key: crypto.randomUUID() } })
      .then((space) => {
        navigate({ view: 'space', id: space.id })
        onClose()
      })
      .catch((error) => {
        setBusy(false)
        reportError(error)
      })
  }
  return (
    <Sheet
      title="New space"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!title.trim() || busy} onClick={create}>
            Create
          </Button>
        </>
      }
    >
      <form
        className={styles.stack}
        onSubmit={(event) => {
          event.preventDefault()
          create()
        }}
      >
        <Field label="Name">
          <input className={inputClass} autoFocus value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <IconPicker value={icon} onChange={setIcon} />
      </form>
    </Sheet>
  )
}

export function RenameSheet({ space, onClose }: { space: Space; onClose: () => void }) {
  const { transport } = useApp()
  const [title, setTitle] = useState(space.title)
  const save = () => {
    const next = title.trim()
    if (!next || next === space.title) return onClose()
    transport
      .call('patchSpace', { id: space.id, body: { expected_revision: space.revision, title: next } })
      .then((updated) => {
        onClose()
        toast(`Renamed to ${next}`, {
          action: {
            label: 'Undo',
            run: () =>
              void transport
                .call('patchSpace', { id: space.id, body: { expected_revision: updated.revision, title: space.title } })
                .catch(reportError),
          },
        })
      })
      .catch(reportError)
  }
  return (
    <Sheet
      title="Rename"
      width={420}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!title.trim()} onClick={save}>
            Save
          </Button>
        </>
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault()
          save()
        }}
      >
        <Field label="Name">
          <input
            className={inputClass}
            autoFocus
            value={title}
            maxLength={200}
            onFocus={(event) => event.target.select()}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
      </form>
    </Sheet>
  )
}

export function IconSheet({ space, onClose }: { space: Space; onClose: () => void }) {
  const { transport } = useApp()
  const [icon, setIcon] = useState<Icon>(space.icon)
  return (
    <Sheet
      title="Change icon"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            onClick={() => {
              transport
                .call('patchSpace', { id: space.id, body: { expected_revision: space.revision, icon } })
                .then(onClose)
                .catch(reportError)
            }}
          >
            Save
          </Button>
        </>
      }
    >
      <IconPicker value={icon} onChange={setIcon} />
    </Sheet>
  )
}

const POLICY = [
  {
    value: 'archive',
    title: 'Archive',
    body: 'Hide this space. Its automations keep delivering here.',
  },
  {
    value: 'keep_and_move',
    title: 'Keep automation and move results to General',
    body: 'Automations keep running. Then this space is deleted.',
  },
  {
    value: 'stop_and_delete',
    title: 'Stop automation and delete space',
    body: 'Jobs are removed from the scheduler. Their script files are not deleted.',
  },
] as const

const STAGES: Record<string, string> = {
  requested: 'Starting',
  provider_change_pending: 'Waiting for the scheduler',
  provider_change_confirmed: 'Confirmed',
  space_deleted: 'Deleted',
}

export function DeleteSheet({ space, onClose }: { space: Space; onClose: () => void }) {
  const { transport } = useApp()
  const archive = useArchiveSpace()
  const jobs = useAutomations().filter((job) => job.space_id === space.id)
  const [choice, setChoice] = useState<(typeof POLICY)[number]['value']>('archive')
  const [operationId, setOperationId] = useState<string | null>(space.pending_operation_id ?? null)
  const [key] = useState(() => crypto.randomUUID())
  const operation = useQuery<Operation>({
    queryKey: keys.operation(operationId ?? 'none'),
    queryFn: () => transport.call('getOperation', { id: operationId! }),
    enabled: operationId !== null,
    refetchInterval: 1500,
  }).data
  const failed = operation?.state === 'failed'
  const pending = operationId !== null && !failed

  useEffect(() => {
    if (operation?.state === 'space_deleted') onClose()
  }, [operation?.state, onClose])

  const start = () => {
    if (choice === 'archive') {
      archive(space)
      onClose()
      return
    }
    transport
      .call('deleteSpace', { id: space.id, body: { policy: choice, expected_revision: space.revision, idempotency_key: key } })
      .then((created) => setOperationId(created.id))
      .catch(reportError)
  }
  const plainDelete = () => {
    transport
      .call('deleteSpace', { id: space.id, body: { policy: 'delete', expected_revision: space.revision, idempotency_key: key } })
      .then((created) => setOperationId(created.id))
      .catch(reportError)
  }

  if (!jobs.length && !operationId) {
    return (
      <Sheet
        title={`Delete ${space.title}?`}
        width={440}
        onClose={onClose}
        footer={
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="danger" onClick={plainDelete}>
              Delete space
            </Button>
          </>
        }
      >
        <p className={styles.note}>This removes the space from your list. Files stay in Library.</p>
      </Sheet>
    )
  }

  return (
    <Sheet
      title={`Delete ${space.title}?`}
      onClose={onClose}
      footer={
        pending || failed ? (
          <>
            {failed && (
              <Button
                onClick={() => {
                  void transport.call('dismissOperation', { id: operationId! }).then(onClose).catch(reportError)
                }}
              >
                Keep space
              </Button>
            )}
            {failed ? (
              <Button
                variant="primary"
                onClick={() => {
                  void transport.call('retryOperation', { id: operationId! }).catch(reportError)
                }}
              >
                Retry
              </Button>
            ) : (
              <Button onClick={onClose}>Close</Button>
            )}
          </>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant={choice === 'archive' ? 'primary' : 'danger'} onClick={start}>
              {choice === 'archive' ? 'Archive' : 'Continue'}
            </Button>
          </>
        )
      }
    >
      <div className={styles.stack}>
        <ul className={styles.jobs}>
          {jobs.map((job) => (
            <li key={job.id} className={styles.job}>
              <Symbol name="clockBadge" size={16} />
              <span>{job.name}</span>
              {operation && <span className={styles.jobState}>{(operation.progress.jobs as any)?.[job.id] ?? ''}</span>}
            </li>
          ))}
        </ul>
        {operationId ? (
          failed ? (
            <div className={styles.alert} role="alert">
              <Symbol name="alert" size={18} />
              <span>{operation?.error_message}</span>
            </div>
          ) : (
            <p className={styles.note} role="status">
              {STAGES[operation?.state ?? 'requested'] ?? 'Working'}
            </p>
          )
        ) : (
          <div className={styles.stack} role="radiogroup" aria-label="What happens to the automation">
            {POLICY.map((option) => (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={choice === option.value}
                className={styles.choice}
                onClick={() => setChoice(option.value)}
              >
                <span className={styles.radio} />
                <span className={styles.choiceTitle}>{option.title}</span>
                <span className={styles.choiceBody}>{option.body}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </Sheet>
  )
}

export function ArchiveSheet({ onClose }: { onClose: () => void }) {
  const { transport } = useApp()
  const { data = [], isError } = useQuery({
    queryKey: keys.archived,
    queryFn: () => transport.call('listSpaces', { query: { include_archived: true } }),
    select: (spaces) => spaces.filter((space) => space.archived_at),
  })
  return (
    <Sheet title="Archived spaces" onClose={onClose}>
      {isError && <p className={styles.muted}>The service is not reachable.</p>}
      {!isError && !data.length && <p className={styles.muted}>Nothing archived.</p>}
      <div className={styles.archived}>
        {data.map((space) => (
          <div key={space.id} className={styles.archivedRow}>
            <SpaceIcon icon={space.icon} size={22} />
            <span className={styles.archivedName}>{space.title}</span>
            <IconButton
              icon="restore"
              label={`Restore ${space.title}`}
              onClick={() => void transport.call('restoreSpace', { id: space.id }).catch(reportError)}
            />
          </div>
        ))}
      </div>
    </Sheet>
  )
}
