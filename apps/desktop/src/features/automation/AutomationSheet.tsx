import { useState } from 'react'
import { useNavigation } from '@/app/navigation'
import { Symbol } from '@/components/Symbol'
import { Button, Sheet, selectClass } from '@/components/ui'
import { zonedTime } from '@/state/format'
import { useApp, useAutomations, useSpaces } from '@/state/queries'
import { reportError, toast } from '@/state/toasts'
import styles from './AutomationSheet.module.css'

export function AutomationSheet({ automationId, onClose }: { automationId: string; onClose: () => void }) {
  const { transport } = useApp()
  const { navigate } = useNavigation()
  const automation = useAutomations().find((item) => item.id === automationId)
  const spaces = useSpaces()
  const [busy, setBusy] = useState(false)
  if (!automation) return null
  const act = (action: 'pause' | 'resume' | 'run_now' | 'redirect', destination?: string) => {
    setBusy(true)
    transport
      .call('automationAction', {
        id: automation.id,
        body: { action, expected_revision: automation.revision, destination_space_id: destination },
      })
      .then(() => {
        if (action === 'run_now') toast('Run requested')
      })
      .catch(reportError)
      .finally(() => setBusy(false))
  }
  const paused = automation.state === 'paused'
  return (
    <Sheet
      title={automation.name}
      onClose={onClose}
      footer={
        <>
          <Button variant="outline" disabled={busy} onClick={() => act(paused ? 'resume' : 'pause')}>
            <Symbol name={paused ? 'play' : 'pause'} size={14} />
            {paused ? 'Resume' : 'Pause'}
          </Button>
          <Button variant="primary" disabled={busy} onClick={() => act('run_now')}>
            Run now
          </Button>
        </>
      }
    >
      <dl className={styles.facts}>
        <dt>Schedule</dt>
        <dd className={styles.mono}>{automation.schedule || 'Not reported'}</dd>
        <dt>Timezone</dt>
        <dd>{automation.timezone}</dd>
        <dt>Next run</dt>
        <dd>{paused ? 'Paused' : zonedTime(automation.next_run_at, automation.timezone)}</dd>
        <dt>Last run</dt>
        <dd>
          {automation.last_run_at ? zonedTime(automation.last_run_at, automation.timezone) : 'Never'}
          {automation.last_outcome && <span className={styles.outcome}>{automation.last_outcome}</span>}
        </dd>
        <dt>Destination</dt>
        <dd>
          <select
            className={selectClass}
            aria-label="Destination"
            value={automation.space_id ?? ''}
            disabled={busy}
            onChange={(event) => act('redirect', event.target.value)}
          >
            {!automation.space_id && <option value="">Not linked</option>}
            {spaces
              .filter((space) => space.kind !== 'code')
              .map((space) => (
                <option key={space.id} value={space.id}>
                  {space.title}
                </option>
              ))}
          </select>
        </dd>
      </dl>
      {automation.stale && (
        <p className={styles.stale} role="status">
          <Symbol name="warning" size={16} />
          Status may be out of date. The scheduler has not reported recently.
        </p>
      )}
      {automation.space_id && (
        <Button
          className={styles.open}
          onClick={() => {
            onClose()
            navigate({ view: 'space', id: automation.space_id! })
          }}
        >
          Open destination
        </Button>
      )}
    </Sheet>
  )
}
