import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from 'react'
import { Symbol } from '@/components/Symbol'
import { IconButton } from '@/components/ui'
import { touch } from '@/state/layout'
import { loadDraft, saveDraft } from '@/state/drafts'
import { fileSize } from '@/state/format'
import { useActiveReply } from '@/state/messages'
import { usePreferences } from '@/state/preferences'
import { useApp } from '@/state/queries'
import { useLink } from '@/state/sync'
import { reportError } from '@/state/toasts'
import type { ArtifactRef } from '@/transport/operations'
import type { UploadSource } from '@/transport/types'
import styles from './Composer.module.css'
import { fileSymbol } from './Message'

interface Staged {
  id: string
  name: string
  size: number
  mime: string
  source?: UploadSource
  sent: number
  artifact?: ArtifactRef
  error?: string
  abort?: AbortController
}

export interface ComposerHandle {
  addFiles: (files: UploadSource[]) => void
  focus: () => void
}

const newId = () => crypto.randomUUID()

const PICTURE_NAME = /\.(png|jpe?g|gif|webp|heic|avif|bmp|tiff?)$/i
const PREVIEW_LIMIT = 25 * 1024 * 1024

/** SVG is excluded: previews treat it as text everywhere, because it can carry script. */
const isPicture = (item: Staged) =>
  item.mime ? item.mime.startsWith('image/') && item.mime !== 'image/svg+xml' : PICTURE_NAME.test(item.name)

/** A local file shows at once. A native path or restored draft shows once its upload can be read back. */
function Picture({ item, onBroken }: { item: Staged; onBroken: () => void }) {
  const { transport } = useApp()
  const [url, setUrl] = useState<string | null>(null)
  const file = item.source instanceof File ? item.source : null
  const artifactId = item.artifact?.id
  useEffect(() => {
    let cancelled = false
    let created: string | null = null
    const show = (blob: Blob) => {
      if (cancelled) return
      created = URL.createObjectURL(blob)
      setUrl(created)
    }
    if (file) show(file)
    else if (artifactId && item.size <= PREVIEW_LIMIT) void transport.fetchBytes(artifactId).then(show, onBroken)
    return () => {
      cancelled = true
      if (created) URL.revokeObjectURL(created)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file, artifactId, item.size, transport])
  if (!url) return <Symbol name="fileImage" size={24} />
  return <img className={styles.tileImage} src={url} alt={item.name} onError={onBroken} />
}

export function Composer({
  spaceId,
  placeholder,
  onSend,
  ref,
}: {
  spaceId: string
  placeholder: string
  onSend?: () => void
  ref?: Ref<ComposerHandle>
}) {
  const { transport, sync } = useApp()
  const prefs = usePreferences()
  const link = useLink()
  const scope = link.serviceId ?? 'local'
  const active = useActiveReply(spaceId)
  const area = useRef<HTMLTextAreaElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const [text, setText] = useState('')
  const [staged, setStaged] = useState<Staged[]>([])
  const [broken, setBroken] = useState<Set<string>>(new Set())
  const composing = useRef(false)
  const loading = useRef(true)

  useEffect(() => {
    loading.current = true
    const draft = loadDraft(scope, spaceId)
    setText(draft.text)
    setStaged(
      draft.artifacts.map((artifact) => ({
        id: artifact.id,
        name: artifact.display_name,
        size: artifact.size,
        mime: artifact.mime,
        sent: artifact.size,
        artifact,
      })),
    )
    if (!touch) area.current?.focus({ preventScroll: true })
  }, [scope, spaceId])

  useEffect(() => {
    // The commit that loads a draft has not applied it yet; saving here would erase it.
    if (loading.current) {
      loading.current = false
      return
    }
    const artifacts = staged.flatMap((item) => (item.artifact ? [item.artifact] : []))
    saveDraft(scope, spaceId, { text, artifacts })
  }, [scope, spaceId, text, staged])

  useLayoutEffect(() => {
    const element = area.current
    const form = element?.closest('form')
    if (!element || !form) return
    // Measuring needs height: auto, which would briefly enlarge the transcript and clamp its scroll position.
    form.style.minHeight = `${form.offsetHeight}px`
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
    form.style.minHeight = ''
  }, [text, prefs.textSize])

  const begin = useCallback(
    (item: Staged) => {
      const abort = new AbortController()
      setStaged((list) => list.map((entry) => (entry.id === item.id ? { ...entry, abort, error: undefined, sent: 0 } : entry)))
      transport
        .upload(item.source!, (sent) => setStaged((list) => list.map((e) => (e.id === item.id ? { ...e, sent } : e))), abort.signal)
        .then((artifact) =>
          setStaged((list) => list.map((e) => (e.id === item.id ? { ...e, artifact, sent: e.size, abort: undefined } : e))),
        )
        .catch((error: Error & { code?: string }) => {
          if (error.code === 'aborted') return
          setStaged((list) => list.map((e) => (e.id === item.id ? { ...e, error: error.message, abort: undefined } : e)))
        })
    },
    [transport],
  )

  const addFiles = useCallback(
    (files: UploadSource[]) => {
      const items: Staged[] = files.map((source) => ({
        id: newId(),
        name: source.name,
        size: source.size,
        mime: source instanceof File ? source.type : '',
        source,
        sent: 0,
      }))
      setStaged((list) => [...list, ...items])
      items.forEach(begin)
    },
    [begin],
  )

  useImperativeHandle(ref, () => ({ addFiles, focus: () => area.current?.focus() }), [addFiles])

  const uploading = staged.some((item) => !item.artifact && !item.error)
  const ready = staged.filter((item) => item.artifact)
  const canSend = Boolean(text.trim() || ready.length) && !uploading

  const send = () => {
    if (!canSend) return
    onSend?.()
    void transport.enqueue({
      client_message_id: newId(),
      space_id: spaceId,
      markdown: text.trim(),
      artifacts: ready.map((item) => item.artifact!),
    })
    setText('')
    setStaged((list) => list.filter((item) => item.error))
    sync.markRead(spaceId)
    area.current?.focus()
  }

  const stop = () => {
    if (active?.run_id) void transport.call('cancelRun', { id: active.run_id }).catch(reportError)
  }

  const browse = async () => {
    const files = await transport.pickFiles()
    if (files) addFiles(files)
    else picker.current?.click()
  }

  const showStop = Boolean(active) && !text.trim() && !ready.length
  const stopping = active?.status === 'cancel_requested'

  return (
    <form
      className={styles.composer}
      onSubmit={(event) => {
        event.preventDefault()
        send()
      }}
    >
      {staged.length > 0 && (
        <div className={styles.chips}>
          {staged.map((item) =>
            isPicture(item) && !item.error && !broken.has(item.id) ? (
              <div key={item.id} className={styles.tile} title={item.name}>
                <Picture item={item} onBroken={() => setBroken((set) => new Set(set).add(item.id))} />
                <IconButton
                  className={styles.tileRemove}
                  icon="close"
                  size={12}
                  label={`Remove ${item.name}`}
                  onClick={() => {
                    item.abort?.abort()
                    setStaged((list) => list.filter((entry) => entry.id !== item.id))
                  }}
                />
                {!item.artifact && (
                  <div className={styles.progress}>
                    <div className={styles.progressBar} style={{ width: `${item.size ? (item.sent / item.size) * 100 : 0}%` }} />
                  </div>
                )}
              </div>
            ) : (
              <div key={item.id} className={`${styles.chip} ${item.error ? styles.chipFailed : ''}`}>
                <span className={styles.chipIcon}>
                  <Symbol name={fileSymbol(item.mime, item.name)} size={20} />
                </span>
                <span className={styles.chipText}>
                  <span className={styles.chipName}>{item.name}</span>
                  <span className={styles.chipMeta}>
                    {item.error ?? (item.artifact ? fileSize(item.size) : `${fileSize(item.sent)} of ${fileSize(item.size)}`)}
                  </span>
                </span>
                {item.error && item.source && (
                  <IconButton className={styles.chipButton} icon="retry" size={14} label={`Retry ${item.name}`} onClick={() => begin(item)} />
                )}
                <IconButton
                  className={styles.chipButton}
                  icon="close"
                  size={14}
                  label={`Remove ${item.name}`}
                  onClick={() => {
                    item.abort?.abort()
                    setStaged((list) => list.filter((entry) => entry.id !== item.id))
                  }}
                />
                {!item.artifact && !item.error && (
                  <div className={styles.progress}>
                    <div className={styles.progressBar} style={{ width: `${item.size ? (item.sent / item.size) * 100 : 0}%` }} />
                  </div>
                )}
              </div>
            ),
          )}
        </div>
      )}
      <div className={styles.row}>
        <textarea
          ref={area}
          className={styles.input}
          rows={1}
          value={text}
          placeholder={placeholder}
          aria-label="Message"
          onChange={(event) => setText(event.target.value)}
          onCompositionStart={() => (composing.current = true)}
          onCompositionEnd={() => (composing.current = false)}
          onPaste={(event) => {
            const files = [...event.clipboardData.files]
            if (files.length) {
              event.preventDefault()
              addFiles(files)
            }
          }}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return
            const wantsSend = prefs.enterSends ? !event.shiftKey : event.metaKey || event.ctrlKey
            if (wantsSend) {
              event.preventDefault()
              send()
            }
          }}
        />
        <IconButton className={styles.attach} icon="attach" size={20} label="Attach files" onClick={() => void browse()} />
        <input
          ref={picker}
          type="file"
          multiple
          hidden
          aria-hidden="true"
          tabIndex={-1}
          onChange={(event) => {
            addFiles([...(event.target.files ?? [])])
            event.target.value = ''
          }}
        />
        {showStop ? (
          <button
            type="button"
            className={`${styles.send} ${styles.stop}`}
            aria-label={stopping ? 'Stopping' : 'Stop'}
            disabled={stopping}
            onClick={stop}
          >
            <Symbol name="stop" size={18} />
          </button>
        ) : (
          <button type="submit" className={styles.send} aria-label="Send" disabled={!canSend}>
            <Symbol name="send" size={24} />
          </button>
        )}
      </div>
    </form>
  )
}
