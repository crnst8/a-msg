import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigation } from '@/app/navigation'
import { Symbol } from '@/components/Symbol'
import { Button, IconButton, Segmented, Sheet, inputClass, selectClass } from '@/components/ui'
import { fileSymbol } from '@/features/conversation/Message'
import { fileDate, fileKind, fileSize, shortTime } from '@/state/format'
import { useApp, useSpaces } from '@/state/queries'
import { keys } from '@/state/sync'
import { reportError, toast } from '@/state/toasts'
import { ServiceError, type Artifact } from '@/transport/operations'
import styles from './Library.module.css'
import { ArtifactPreview } from './Preview'

const TYPES = [
  { value: 'all', label: 'All' },
  { value: 'text', label: 'Text' },
  { value: 'image', label: 'Images' },
  { value: 'pdf', label: 'PDF' },
  { value: 'audio', label: 'Audio' },
  { value: 'video', label: 'Video' },
  { value: 'other', label: 'Other' },
]

export function Library({ initial }: { initial?: string }) {
  const { transport } = useApp()
  const spaces = useSpaces()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [type, setType] = useState('all')
  const [source, setSource] = useState('')
  const [hidden, setHidden] = useState(false)
  const [selected, setSelected] = useState<string | undefined>(initial)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query), 180)
    return () => clearTimeout(timer)
  }, [query])

  const list = useInfiniteQuery({
    queryKey: [...keys.artifacts, 'list', debounced, type, source, hidden],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      transport.call('listArtifacts', {
        query: {
          q: debounced || undefined,
          type: type === 'all' ? undefined : type,
          space_id: source || undefined,
          include_hidden: hidden,
          offset: pageParam,
          limit: 50,
        },
      }),
    getNextPageParam: (last) => last.next_offset ?? undefined,
  })
  const items = list.data?.pages.flatMap((page) => page.items) ?? []
  const filtered = Boolean(debounced || source || type !== 'all')

  return (
    <div className={styles.view} data-detail={Boolean(selected)}>
      <section className={styles.main} aria-label="Library">
        <div className={styles.titleRow}>
          <h1 className={styles.title}>Library</h1>
          <Button aria-pressed={hidden} onClick={() => setHidden((value) => !value)}>
            <Symbol name={hidden ? 'show' : 'hide'} size={16} />
            {hidden ? 'Showing hidden' : 'Hidden'}
          </Button>
        </div>
        <div className={styles.filters}>
          <div className={styles.search}>
            <span className={styles.searchIcon}>
              <Symbol name="search" size={18} />
            </span>
            <input
              className={`${inputClass} ${styles.searchInput}`}
              value={query}
              placeholder="Search files"
              aria-label="Search files"
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <select
            className={`${selectClass} ${styles.sourceSelect}`}
            aria-label="Source space"
            value={source}
            onChange={(event) => setSource(event.target.value)}
          >
            <option value="">All spaces</option>
            {spaces.map((space) => (
              <option key={space.id} value={space.id}>
                {space.title}
              </option>
            ))}
          </select>
          <Segmented label="File type" value={type} options={TYPES} onChange={setType} />
        </div>
        <div className={styles.list} role="listbox" aria-label="Files">
          {list.isError && <p className={styles.empty}>The service is not reachable.</p>}
          {!list.isError && list.isSuccess && !items.length && (
            <p className={styles.empty}>{filtered ? 'No matching files.' : 'Files from your conversations appear here.'}</p>
          )}
          {items.map((artifact) => (
            <button
              key={artifact.id}
              type="button"
              role="option"
              aria-selected={selected === artifact.id}
              className={styles.row}
              onClick={() => setSelected(artifact.id)}
            >
              <span className={styles.rowIcon}>
                <Symbol name={fileSymbol(artifact.mime, artifact.display_name)} size={24} />
              </span>
              <span className={styles.rowText}>
                <span className={`${styles.rowName} ${artifact.hidden ? styles.rowHidden : ''}`}>{artifact.display_name}</span>
                <span className={styles.rowMeta}>
                  {artifact.origin_space_title ?? 'No space'}, {fileSize(artifact.size)}
                </span>
              </span>
              <span className={styles.rowDate}>{fileDate(artifact.updated_at)}</span>
            </button>
          ))}
          {list.hasNextPage && (
            <Button className={styles.more} variant="outline" onClick={() => void list.fetchNextPage()}>
              Show more
            </Button>
          )}
        </div>
      </section>
      {selected && <Detail id={selected} onClose={() => setSelected(undefined)} />}
    </div>
  )
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const { transport } = useApp()
  const { navigate } = useNavigation()
  const client = useQueryClient()
  const { data } = useQuery({
    queryKey: [...keys.artifacts, 'detail', id],
    queryFn: () => transport.call('getArtifact', { id }),
  })
  const [version, setVersion] = useState<number | undefined>()
  const [confirming, setConfirming] = useState(false)
  useEffect(() => setVersion(undefined), [id])
  if (!data) return <aside className={styles.detail} />

  const save = () => {
    transport
      .save(data, version)
      .then((path) => {
        if (path) {
          toast('Saved', { action: { label: 'Reveal', run: () => void transport.reveal(path).catch(reportError) } })
        }
      })
      .catch(reportError)
  }

  const hide = () => {
    transport
      .call(data.hidden ? 'unhideArtifact' : 'hideArtifact', { id })
      .then(() => {
        client.invalidateQueries({ queryKey: keys.artifacts })
        toast(data.hidden ? 'Shown in Library' : 'Hidden from Library', {
          action: data.hidden
            ? undefined
            : {
                label: 'Undo',
                run: () =>
                  void transport
                    .call('unhideArtifact', { id })
                    .then(() => client.invalidateQueries({ queryKey: keys.artifacts })),
              },
        })
      })
      .catch(reportError)
  }

  return (
    <aside className={styles.detail} aria-label={data.display_name}>
      <div className={styles.detailHead}>
        <h2 className={styles.detailName}>{data.display_name}</h2>
        <IconButton icon="close" label="Close details" onClick={onClose} />
      </div>
      <div className={styles.detailBody}>
        <ArtifactPreview artifact={data} version={version} />
        <div className={styles.actions}>
          <Button variant="primary" onClick={save}>
            <Symbol name="download" size={16} />
            Save
          </Button>
          {transport.kind === 'tauri' && (
            <Button variant="outline" onClick={() => void transport.openFile(data, version).catch(reportError)}>
              Open
            </Button>
          )}
          <Button variant="outline" onClick={hide}>
            {data.hidden ? 'Show' : 'Hide'}
          </Button>
          <Button variant="outline" onClick={() => setConfirming(true)}>
            Delete
          </Button>
        </div>
        <dl className={styles.facts}>
          <dt>Type</dt>
          <dd>{fileKind(data.mime, data.display_name)}</dd>
          <dt>Size</dt>
          <dd>{fileSize(data.size)}</dd>
          <dt>Added</dt>
          <dd>{shortTime(data.created_at)}</dd>
          <dt>From</dt>
          <dd>
            {data.origin_space_id && data.origin_space_title ? (
              <button
                type="button"
                className={styles.linkButton}
                onClick={() =>
                  navigate({
                    view: 'space',
                    id: data.origin_space_id!,
                    focusMessage: data.origin_message_id ?? undefined,
                  })
                }
              >
                {data.origin_space_title}
              </button>
            ) : (
              'No space'
            )}
          </dd>
          <dt>SHA-256</dt>
          <dd className={styles.checksum} title={data.checksum}>
            {data.checksum.slice(0, 16)}
          </dd>
        </dl>
        {data.versions.length > 1 && (
          <div>
            <div className={styles.sectionLabel}>Versions</div>
            <div className={styles.versions}>
              {data.versions.map((item) => (
                <div key={item.version as number} className={styles.version}>
                  <span className={styles.versionNumber}>v{item.version as number}</span>
                  <span className={styles.versionDate}>{shortTime(item.created_at as string)}</span>
                  <Button onClick={() => setVersion(item.version as number)}>View</Button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      {confirming && <DeleteFile artifact={data} onClose={() => setConfirming(false)} onDeleted={onClose} />}
    </aside>
  )
}

function DeleteFile({
  artifact,
  onClose,
  onDeleted,
}: {
  artifact: Artifact
  onClose: () => void
  onDeleted: () => void
}) {
  const { transport } = useApp()
  const client = useQueryClient()
  const remove = () => {
    transport
      .call('deleteArtifact', { id: artifact.id, query: { confirm: true } })
      .then(() => {
        client.invalidateQueries({ queryKey: keys.artifacts })
        onClose()
        onDeleted()
        toast('File deleted')
      })
      .catch((error: ServiceError) => reportError(error))
  }
  return (
    <Sheet
      title={`Delete ${artifact.display_name}?`}
      width={460}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" onClick={remove}>
            Delete permanently
          </Button>
        </>
      }
    >
      {artifact.references.length > 0 ? (
        <>
          <p style={{ marginBottom: 10 }}>
            These messages stay, with this file marked as deleted:
          </p>
          <ul className={styles.references}>
            {artifact.references.map((reference) => (
              <li key={reference.message_id as string}>
                {reference.space_title as string}
                <span className={styles.referenceText}>{reference.excerpt as string}</span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>The stored file is removed.</p>
      )}
    </Sheet>
  )
}
