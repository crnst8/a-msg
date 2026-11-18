import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Symbol } from '@/components/Symbol'
import { Button, Sheet } from '@/components/ui'
import { fileSymbol } from '@/features/conversation/Message'
import { Markdown } from '@/features/conversation/Markdown'
import { fileKind, fileSize } from '@/state/format'
import { useApp } from '@/state/queries'
import { reportError } from '@/state/toasts'
import type { ArtifactRef } from '@/transport/operations'
import styles from './Preview.module.css'

const TEXT_LIMIT = 1024 * 1024

function textual(mime: string): boolean {
  return mime.startsWith('text/') || ['application/json', 'application/x-yaml', 'application/xml', 'image/svg+xml'].includes(mime)
}

/** Each format has its own handler. HTML and SVG stay text; nothing from a file runs in the app origin. */
export function ArtifactPreview({ artifact, version }: { artifact: ArtifactRef; version?: number }) {
  const { transport } = useApp()
  const [url, setUrl] = useState<string | null>(null)
  const [text, setText] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const { mime, size } = artifact
  const isText = textual(mime)
  const canShow = artifact.previewable && artifact.state === 'ready' && (!isText || size <= TEXT_LIMIT)

  useEffect(() => {
    if (!canShow) return
    let cancelled = false
    let created: string | null = null
    setUrl(null)
    setText(null)
    setFailed(false)
    transport
      .fetchBytes(artifact.id, version)
      .then(async (blob) => {
        if (cancelled) return
        if (isText) setText(await blob.text())
        else {
          created = URL.createObjectURL(blob.slice(0, blob.size, mime))
          setUrl(created)
        }
      })
      .catch(() => !cancelled && setFailed(true))
    return () => {
      cancelled = true
      if (created) URL.revokeObjectURL(created)
    }
  }, [artifact.id, version, transport, canShow, isText, mime])

  const fallback = (
    <div className={styles.none}>
      <Symbol name={fileSymbol(mime, artifact.display_name)} size={36} />
      <span>{failed ? 'This file could not be loaded.' : fileKind(mime, artifact.display_name)}</span>
    </div>
  )
  if (!canShow || failed) return <div className={styles.frame}>{fallback}</div>
  if (text !== null) {
    return (
      <div className={styles.frame}>
        {mime === 'text/markdown' ? (
          <div className={styles.markdown}>
            <Markdown text={text} />
          </div>
        ) : (
          <pre className={styles.text} tabIndex={0}>
            {text}
          </pre>
        )}
      </div>
    )
  }
  if (!url) return <div className={styles.frame} />
  return (
    <div className={styles.frame}>
      {mime.startsWith('image/') && <img className={styles.image} src={url} alt={artifact.display_name} />}
      {mime === 'application/pdf' && <iframe className={styles.pdf} src={url} title={artifact.display_name} sandbox="" />}
      {mime.startsWith('audio/') && <audio className={styles.media} src={url} controls />}
      {mime.startsWith('video/') && <video className={styles.media} src={url} controls />}
    </div>
  )
}

export function PreviewSheet({ artifactId, onClose }: { artifactId: string; onClose: () => void }) {
  const { transport } = useApp()
  const { data } = useQuery({
    queryKey: ['artifacts', artifactId],
    queryFn: () => transport.call('getArtifact', { id: artifactId }),
  })
  if (!data) return null
  return (
    <Sheet
      title={data.display_name}
      width={760}
      onClose={onClose}
      footer={
        <>
          <span style={{ marginRight: 'auto', alignSelf: 'center', color: 'var(--text-2)', fontSize: '.8125rem' }}>
            {fileSize(data.size)}
          </span>
          <Button variant="primary" onClick={() => void transport.save(data).catch(reportError)}>
            Save
          </Button>
        </>
      }
    >
      <ArtifactPreview artifact={data} />
    </Sheet>
  )
}
