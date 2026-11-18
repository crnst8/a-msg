import { useEffect, useState } from 'react'
import { useNavigation } from '@/app/navigation'
import { Symbol } from '@/components/Symbol'
import { Button, Field, Segmented, Sheet, inputClass, monoClass, selectClass, textareaClass } from '@/components/ui'
import { useApp, useConnections } from '@/state/queries'
import { reportError } from '@/state/toasts'
import type { ConfigFile, ConfigPatch } from '@/transport/operations'
import { ACCESS_NAMES, accessOptions, type Access } from './access'
import styles from './Code.module.css'

export function NewConnectionSheet({ onClose }: { onClose: () => void }) {
  const { transport } = useApp()
  const [name, setName] = useState('')
  const [kind, setKind] = useState<'codex' | 'claude' | 'bridge'>('codex')
  const [roots, setRoots] = useState('')
  const [command, setCommand] = useState('')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const create = () => {
    setBusy(true)
    transport
      .call('createConnection', {
        body: {
          name: name.trim() || (kind === 'codex' ? 'Codex' : kind === 'claude' ? 'Claude Code' : 'Bridge'),
          kind,
          config: {
            allowed_roots: roots.split('\n').map((line) => line.trim()).filter(Boolean),
            ...(command.trim() ? { command: command.trim() } : {}),
          },
          secrets: secret && kind !== 'bridge' ? { api_key: secret } : {},
        },
      })
      .then(onClose)
      .catch((error) => {
        setBusy(false)
        reportError(error)
      })
  }
  return (
    <Sheet
      title="Add connection"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={busy || !roots.trim()} onClick={create}>
            Add
          </Button>
        </>
      }
    >
      <div className={styles.stack}>
        <Segmented
          label="Agent"
          value={kind}
          onChange={setKind}
          options={[
            { value: 'codex', label: 'Codex' },
            { value: 'claude', label: 'Claude Code' },
            { value: 'bridge', label: 'Bridge' },
          ]}
        />
        <Field label="Name">
          <input className={inputClass} value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Allowed folders, one per line">
          <textarea
            className={`${textareaClass} ${monoClass}`}
            rows={3}
            value={roots}
            placeholder="/home/you/projects"
            onChange={(event) => setRoots(event.target.value)}
          />
        </Field>
        {kind !== 'bridge' && (
          <Field label="Program path">
            <input
              className={`${inputClass} ${monoClass}`}
              value={command}
              placeholder={kind}
              onChange={(event) => setCommand(event.target.value)}
            />
          </Field>
        )}
        {kind !== 'bridge' && (
          <Field label="API key, optional">
            <input
              className={inputClass}
              type="password"
              autoComplete="off"
              value={secret}
              onChange={(event) => setSecret(event.target.value)}
            />
          </Field>
        )}
      </div>
    </Sheet>
  )
}

export function NewSessionSheet({ connectionId, onClose }: { connectionId?: string; onClose: () => void }) {
  const { transport, sync } = useApp()
  const { navigate } = useNavigation()
  const connections = useConnections().filter((item) => item.kind === 'codex' || item.kind === 'claude' || item.kind === 'bridge')
  const [connection, setConnection] = useState(connectionId ?? connections[0]?.id ?? '')
  const [directory, setDirectory] = useState('')
  const [title, setTitle] = useState('')
  const selected = connections.find((item) => item.id === connection)
  const roots = (selected?.config?.allowed_roots as string[] | undefined) ?? []
  const options = accessOptions(selected)
  const [chosen, setChosen] = useState<Access | null>(null)
  const access = chosen && options?.levels.includes(chosen) ? chosen : (options?.fallback ?? null)
  const create = () => {
    sync.expectSpace()
    const folder = directory.trim() || roots[0] || ''
    transport
      .call('createSpace', {
        body: {
          kind: 'code',
          title: title.trim() || folder.split('/').filter(Boolean).pop() || 'Session',
          icon: { kind: 'catalog', value: 'terminal' },
          agent_connection_id: connection,
          working_directory: folder,
          ...(options && access ? { access } : {}),
          idempotency_key: crypto.randomUUID(),
        },
      })
      .then((space) => {
        onClose()
        navigate({ view: 'space', id: space.id })
      })
      .catch(reportError)
  }
  return (
    <Sheet
      title="New session"
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!connection || !(directory.trim() || roots[0])} onClick={create}>
            Start
          </Button>
        </>
      }
    >
      <div className={styles.stack}>
        <Field label="Agent">
          <select className={selectClass} value={connection} onChange={(event) => setConnection(event.target.value)}>
            {connections.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Working directory">
          <input
            className={`${inputClass} ${monoClass}`}
            value={directory}
            placeholder={roots[0] ?? ''}
            onChange={(event) => setDirectory(event.target.value)}
          />
        </Field>
        {options && access && (
          <Field label="Access" group>
            <Segmented
              label="Access"
              value={access}
              onChange={setChosen}
              options={options.levels.map((level) => ({ value: level, label: ACCESS_NAMES[level] }))}
            />
          </Field>
        )}
        <Field label="Name">
          <input className={inputClass} value={title} onChange={(event) => setTitle(event.target.value)} />
        </Field>
      </div>
    </Sheet>
  )
}

function Diff({ text }: { text: string }) {
  return (
    <pre className={styles.diff} tabIndex={0}>
      {text.split('\n').map((line, index) => (
        <span
          key={index}
          className={
            line.startsWith('+') && !line.startsWith('+++')
              ? styles.diffAdd
              : line.startsWith('-') && !line.startsWith('---')
                ? styles.diffDel
                : styles.diffLine
          }
        >
          {line || ' '}
        </span>
      ))}
    </pre>
  )
}

export function ConfigSheet({ connectionId, onClose }: { connectionId: string; onClose: () => void }) {
  const { transport } = useApp()
  const [file, setFile] = useState<ConfigFile | null>(null)
  const [text, setText] = useState('')
  const [patch, setPatch] = useState<ConfigPatch | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    transport
      .call('readConfig', { id: connectionId })
      .then((config) => {
        setFile(config)
        setText(config.content)
      })
      .catch((failure: Error) => setError(failure.message))
  }, [connectionId, transport])

  const prepare = () => {
    transport
      .call('proposePatch', {
        id: connectionId,
        body: { path: file!.path, proposed: text, base_checksum: file!.checksum },
      })
      .then((created) => {
        setError(null)
        setPatch(created)
      })
      .catch((failure: Error) => setError(failure.message))
  }
  const apply = () => {
    transport
      .call('applyPatch', { id: patch!.id })
      .then(setPatch)
      .catch((failure: Error) => setError(failure.message))
  }
  const restore = () => {
    transport
      .call('rollbackPatch', { id: patch!.id })
      .then(setPatch)
      .catch((failure: Error) => setError(failure.message))
  }

  const applied = patch?.state === 'applied'
  const unhealthy = patch?.state === 'unhealthy'
  const restored = patch?.state === 'rolled_back'

  return (
    <Sheet
      title="Configuration"
      width={720}
      onClose={onClose}
      footer={
        !patch ? (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button variant="primary" disabled={!file || text === file.content} onClick={prepare}>
              Review changes
            </Button>
          </>
        ) : patch.state === 'proposed' ? (
          <>
            <Button onClick={() => setPatch(null)}>Cancel</Button>
            <Button variant="primary" onClick={apply}>
              Apply
            </Button>
          </>
        ) : applied || unhealthy ? (
          <>
            <Button onClick={restore} variant={unhealthy ? 'primary' : 'outline'}>
              Restore Previous Configuration
            </Button>
            {applied && (
              <Button variant="primary" onClick={onClose}>
                Done
              </Button>
            )}
          </>
        ) : (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        )
      }
    >
      <div className={styles.stack}>
        {file && <span className={styles.path}>{file.path}</span>}
        {error && (
          <div className={`${styles.result} ${styles.resultBad}`} role="alert">
            <Symbol name="alert" size={18} />
            <span>{error}</span>
          </div>
        )}
        {!patch && file && (
          <textarea
            className={`${textareaClass} ${styles.editor}`}
            aria-label="Configuration"
            value={text}
            spellCheck={false}
            onChange={(event) => setText(event.target.value)}
          />
        )}
        {patch && <Diff text={patch.diff || 'No changes.'} />}
        {(applied || unhealthy || restored) && (
          <div className={`${styles.result} ${unhealthy ? styles.resultBad : styles.resultOk}`} role="status">
            <Symbol name={unhealthy ? 'alert' : 'success'} size={18} />
            <span>
              {restored ? 'Previous configuration restored.' : applied ? 'Health check passed.' : 'Health check failed.'}{' '}
              {patch?.health?.detail as string}
            </span>
          </div>
        )}
      </div>
    </Sheet>
  )
}
