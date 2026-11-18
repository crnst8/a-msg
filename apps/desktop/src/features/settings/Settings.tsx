import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useNavigation } from '@/app/navigation'
import { Button, IconButton, Segmented, Sheet } from '@/components/ui'
import { connectionState, fileDate } from '@/state/format'
import { usePhone } from '@/state/layout'
import { updatePreferences, usePreferences, type RailPosition } from '@/state/preferences'
import { useApp, useConnections } from '@/state/queries'
import { enablePush, disablePush, pushStatus, pushSupported, savePushPreferences } from '@/state/push'
import { useLink } from '@/state/sync'
import {
  PALETTES,
  TOKEN_KEYS,
  activeScheme,
  contrast,
  exportTheme,
  importTheme,
  resetTheme,
  resolveTokens,
  suggestReadable,
  type PaletteName,
  type ThemeTokens,
} from '@/state/theme'
import { reportError, toast } from '@/state/toasts'
import styles from './Settings.module.css'

type Tab = 'appearance' | 'connections' | 'notifications'

export function Settings({ initialTab }: { initialTab?: Tab }) {
  const [tab, setTab] = useState<Tab>(initialTab ?? 'appearance')
  return (
    <div className={styles.view}>
      <div className={styles.panel}>
        <h1 className={styles.title}>Settings</h1>
        <div className={styles.tabs}>
          <Segmented
            label="Settings section"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'appearance', label: 'Appearance' },
              { value: 'connections', label: 'Connections' },
              { value: 'notifications', label: 'Notifications' },
            ]}
          />
        </div>
        {tab === 'appearance' && <Appearance />}
        {tab === 'connections' && <Connections />}
        {tab === 'notifications' && <Notifications />}
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className={styles.row}>
      <span className={styles.rowLabel}>{label}</span>
      {children}
    </div>
  )
}

function Switch({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <Row label={label}>
      <button
        type="button"
        role="switch"
        aria-label={label}
        aria-checked={checked}
        className={styles.switch}
        onClick={() => onChange(!checked)}
      />
    </Row>
  )
}

const LABELS: Record<keyof ThemeTokens, string> = {
  canvas: 'Canvas',
  surface: 'Surface',
  text: 'Text',
  textSecondary: 'Secondary text',
  messageUser: 'Your messages',
  messageUserText: 'Your message text',
  accent: 'Accent',
  onAccent: 'Text on accent',
  rail: 'Rail',
  railIcon: 'Rail icons',
}

const PAIRS: [keyof ThemeTokens, keyof ThemeTokens, number][] = [
  ['text', 'canvas', 4.5],
  ['text', 'surface', 4.5],
  ['textSecondary', 'canvas', 4.5],
  ['textSecondary', 'surface', 4.5],
  ['messageUserText', 'messageUser', 4.5],
  ['onAccent', 'accent', 4.5],
  ['railIcon', 'rail', 3],
]

function Colors() {
  const prefs = usePreferences()
  const scheme = activeScheme(prefs)
  const tokens = resolveTokens(prefs)
  const set = (key: keyof ThemeTokens, value: string) =>
    updatePreferences({
      custom: { ...prefs.custom, [scheme]: { ...prefs.custom[scheme], [key]: value.toUpperCase() } },
    })
  return (
    <div className={styles.colors}>
      {TOKEN_KEYS.map((key) => {
        const failing = PAIRS.find(([fg, bg, target]) => fg === key && contrast(tokens[fg], tokens[bg]) < target)
        const suggestion = failing && suggestReadable(tokens[failing[0]], tokens[failing[1]], failing[2])
        return (
          <div key={key} className={styles.color}>
            <input
              type="color"
              className={styles.colorInput}
              aria-label={LABELS[key]}
              value={tokens[key].toLowerCase()}
              onChange={(event) => set(key, event.target.value)}
            />
            <span className={styles.colorName}>{LABELS[key]}</span>
            <span className={styles.colorHex}>{tokens[key]}</span>
            {failing && suggestion && (
              <span className={styles.warning}>
                {contrast(tokens[failing[0]], tokens[failing[1]]).toFixed(1)}:1 on {LABELS[failing[1]].toLowerCase()}
                <button type="button" className={styles.linkButton} onClick={() => set(key, suggestion)}>
                  Use {suggestion}
                </button>
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

const POSITIONS: { value: RailPosition; label: string; layout: React.CSSProperties; rail: React.CSSProperties }[] = [
  { value: 'left', label: 'Left', layout: { gridTemplateColumns: '10px 1fr' }, rail: {} },
  { value: 'right', label: 'Right', layout: { gridTemplateColumns: '1fr 10px' }, rail: { gridColumn: 2 } },
  { value: 'top', label: 'Top', layout: { gridTemplateRows: '8px 1fr' }, rail: {} },
  { value: 'bottom', label: 'Bottom', layout: { gridTemplateRows: '1fr 8px' }, rail: { gridRow: 2 } },
]

function Appearance() {
  const phone = usePhone()
  const prefs = usePreferences()
  const scheme = activeScheme(prefs)
  const fileInput = useRef<HTMLInputElement>(null)
  const avatarInput = useRef<HTMLInputElement>(null)
  const [importError, setImportError] = useState<string | null>(null)
  const [showColors, setShowColors] = useState(Object.keys(prefs.custom[scheme] ?? {}).length > 0)

  const exportFile = () => {
    const url = URL.createObjectURL(new Blob([exportTheme()], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url
    link.download = 'a-msg-theme.json'
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 5000)
  }

  const setAvatar = async (file: File) => {
    const bitmap = await createImageBitmap(file)
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 96
    const side = Math.min(bitmap.width, bitmap.height)
    canvas
      .getContext('2d')!
      .drawImage(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side, 0, 0, 96, 96)
    updatePreferences({ avatar: canvas.toDataURL('image/png') })
  }

  return (
    <>
      <div className={styles.card}>
        <Row label="Mode">
          <Segmented
            label="Mode"
            value={prefs.mode}
            onChange={(mode) => updatePreferences({ mode })}
            options={[
              { value: 'system', label: 'System' },
              { value: 'light', label: 'Light' },
              { value: 'dark', label: 'Dark' },
            ]}
          />
        </Row>
        <div className={`${styles.row} ${styles.column}`}>
          <span className={styles.rowLabel}>Palette</span>
          <div className={styles.swatches} role="radiogroup" aria-label="Palette">
            {(Object.keys(PALETTES) as PaletteName[]).map((name) => {
              const palette = PALETTES[name][scheme]
              return (
                <button
                  key={name}
                  type="button"
                  role="radio"
                  aria-checked={prefs.palette === name}
                  className={styles.swatch}
                  style={{ background: palette.canvas, color: palette.text }}
                  onClick={() => updatePreferences({ palette: name, custom: {} })}
                >
                  <span className={styles.swatchRail} />
                  <span className={styles.swatchBody}>
                    <span className={styles.swatchName}>{name === 'paper' ? 'Paper' : 'Sage'}</span>
                    <span className={styles.swatchUser} style={{ background: palette.messageUser }} />
                    <span className={styles.swatchAgent} style={{ background: palette.surface }} />
                  </span>
                </button>
              )
            })}
          </div>
        </div>
        <Row label="Colors">
          <Button variant="outline" aria-expanded={showColors} onClick={() => setShowColors((value) => !value)}>
            {showColors ? 'Hide' : 'Customize'}
          </Button>
        </Row>
        {showColors && (
          <div className={`${styles.row} ${styles.column}`}>
            <Colors />
          </div>
        )}
      </div>

      <div className={styles.card}>
        <Row label="Font">
          <Segmented
            label="Font"
            value={prefs.font}
            onChange={(font) => updatePreferences({ font })}
            options={[
              { value: 'haas', label: 'Helvetica' },
              { value: 'system', label: 'System' },
              { value: 'serif', label: 'Serif' },
            ]}
          />
        </Row>
        <Row label="Text size">
          <span className={styles.slider}>
            <input
              type="range"
              aria-label="Text size"
              min={14}
              max={20}
              step={1}
              value={prefs.textSize}
              onChange={(event) => updatePreferences({ textSize: Number(event.target.value) })}
            />
            <span className={styles.rowValue}>{prefs.textSize}</span>
          </span>
        </Row>
        <Row label="Density">
          <Segmented
            label="Density"
            value={prefs.density}
            onChange={(density) => updatePreferences({ density })}
            options={[
              { value: 'comfortable', label: 'Comfortable' },
              { value: 'compact', label: 'Compact' },
            ]}
          />
        </Row>
        {!phone && (
          <div className={`${styles.row} ${styles.column}`}>
            <span className={styles.rowLabel}>Rail position</span>
            <div className={styles.positions} role="radiogroup" aria-label="Rail position">
              {POSITIONS.map((position) => (
                <button
                  key={position.value}
                  type="button"
                  role="radio"
                  aria-checked={prefs.rail === position.value}
                  className={styles.position}
                  onClick={() => updatePreferences({ rail: position.value })}
                >
                  <span className={styles.miniWindow} style={position.layout}>
                    <span className={styles.miniRail} style={position.rail} />
                  </span>
                  {position.label}
                </button>
              ))}
            </div>
          </div>
        )}
        {!phone && (prefs.rail === 'left' || prefs.rail === 'right') && (
          <Switch label="Show labels" checked={prefs.expanded} onChange={(expanded) => updatePreferences({ expanded })} />
        )}
        <Switch
          label="Enter sends"
          checked={prefs.enterSends}
          onChange={(enterSends) => updatePreferences({ enterSends })}
        />
        <Switch
          label="Show agent name"
          checked={prefs.showAgentName}
          onChange={(showAgentName) => updatePreferences({ showAgentName })}
        />
        <Row label="Agent picture">
          <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {prefs.avatar && <img className={styles.avatarPreview} src={prefs.avatar} alt="" />}
            {prefs.avatar && <Button onClick={() => updatePreferences({ avatar: null })}>Remove</Button>}
            <Button variant="outline" onClick={() => avatarInput.current?.click()}>
              Choose
            </Button>
          </span>
          <input
            ref={avatarInput}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) void setAvatar(file).catch(reportError)
              event.target.value = ''
            }}
          />
        </Row>
      </div>

      <div className={styles.card}>
        <div className={styles.actions}>
          <Button variant="outline" onClick={exportFile}>
            Export theme
          </Button>
          <Button variant="outline" onClick={() => fileInput.current?.click()}>
            Import theme
          </Button>
          <Button
            onClick={() => {
              resetTheme()
              setImportError(null)
              setShowColors(false)
            }}
          >
            Reset
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={async (event) => {
              const file = event.target.files?.[0]
              event.target.value = ''
              if (!file) return
              try {
                importTheme(await file.text())
                setImportError(null)
                toast('Theme imported')
              } catch (error) {
                setImportError((error as Error).message)
              }
            }}
          />
        </div>
        {importError && (
          <p className={styles.importError} role="alert">
            {importError}
          </p>
        )}
      </div>
    </>
  )
}

function SignOutSheet({ endpoint, queued, onClose }: { endpoint?: string; queued: number; onClose: () => void }) {
  const { sync, transport } = useApp()
  const [busy, setBusy] = useState(false)
  const signOut = () => {
    setBusy(true)
    sync
      .disconnect(() => transport.signOut())
      .then((revoked) => {
        if (!revoked) toast('Signed out here. The server could not be reached, so it still lists this device.')
      })
      .catch((error) => {
        setBusy(false)
        reportError(error)
      })
  }
  return (
    <Sheet
      title="Sign out"
      width={420}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="danger" disabled={busy} onClick={signOut}>
            {busy ? 'Signing out' : 'Sign out'}
          </Button>
        </>
      }
    >
      <p className={styles.sheetServer}>{endpoint}</p>
      <p className={styles.sheetNote}>
        {transport.kind === 'web' ? 'Signing in again needs the password and a code.' : 'Reconnecting needs a new pairing code.'}
      </p>
      {queued > 0 && (
        <p className={styles.sheetNote}>
          {queued} unsent {queued === 1 ? 'message stays' : 'messages stay'} on this device.
        </p>
      )}
    </Sheet>
  )
}

function Connections() {
  const { sync, transport } = useApp()
  const { navigate, show } = useNavigation()
  const link = useLink()
  const connections = useConnections()
  const health = useQuery({ queryKey: ['health'], queryFn: () => transport.call('health', undefined), refetchInterval: 15000 })
  const devices = useQuery({ queryKey: ['devices'], queryFn: () => transport.call('listDevices', undefined) })
  const info = useQuery({ queryKey: ['pairing'], queryFn: () => transport.pairing() })
  const [signingOut, setSigningOut] = useState(false)
  const activeDevices = devices.data?.filter((device) => !device.revoked_at) ?? []
  const queueKnown = link.state !== 'offline' && !health.isError
  return (
    <>
      <div className={styles.card}>
        <Row label="Server">
          <span className={styles.rowValue}>{info.data?.endpoint ?? ''}</span>
        </Row>
        <Row label="Status">
          <span className={styles.rowValue}>
            {link.state === 'online' ? `Connected, version ${health.data?.version ?? ''}` : link.state === 'connecting' ? 'Reconnecting' : 'Offline'}
          </span>
        </Row>
        {queueKnown && (
          <Row label="Queue">
            <span className={styles.rowValue}>
              {health.data ? `${health.data.queue.active} active, ${health.data.queue.queued} waiting` : ''}
            </span>
          </Row>
        )}
        <div className={styles.actions}>
          {transport.kind !== 'web' && (
            <Button variant="outline" onClick={() => void sync.disconnect(() => transport.leaveServer()).catch(reportError)}>
              Change server
            </Button>
          )}
          <Button variant="outline" onClick={() => setSigningOut(true)}>
            Sign out
          </Button>
        </div>
      </div>
      <div className={styles.card}>
        {connections.map((connection) => (
          <Row key={connection.id} label={connection.name}>
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
              <span className={styles.rowValue}>{connectionState(connection.state)}</span>
              {Boolean(connection.config?.config_path) && (
                <Button variant="outline" onClick={() => show({ kind: 'config', connectionId: connection.id })}>
                  Configuration
                </Button>
              )}
            </span>
          </Row>
        ))}
        <div className={styles.actions}>
          <Button variant="outline" onClick={() => navigate({ view: 'code' })}>
            Code Connector
          </Button>
          <Button variant="outline" onClick={() => show({ kind: 'archive' })}>
            Archived spaces
          </Button>
        </div>
      </div>
      {activeDevices.length > 0 && (
        <div className={styles.card}>
          {activeDevices.map((device) => (
            <div key={device.id} className={styles.device}>
              <span className={styles.deviceName}>
                {device.name}
                {device.current ? ' (this device)' : ''}
              </span>
              <span className={styles.deviceDate}>{fileDate(device.created_at)}</span>
              {!device.current && (
                <IconButton
                  icon="trash"
                  label={`Revoke ${device.name}`}
                  onClick={() =>
                    void transport
                      .call('revokeDevice', { id: device.id })
                      .then(() => devices.refetch())
                      .catch(reportError)
                  }
                />
              )}
            </div>
          ))}
        </div>
      )}
      {signingOut && <SignOutSheet endpoint={info.data?.endpoint} queued={link.queued} onClose={() => setSigningOut(false)} />}
    </>
  )
}

function Notifications() {
  const prefs = usePreferences()
  const { transport } = useApp()
  const web = transport.kind === 'web'
  const supported = pushSupported()
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!web || !supported) return
    void pushStatus()
      .then((status) => setEnabled(status.enabled && Notification.permission === 'granted'))
      .catch(() => setError('Could not load notification settings. Reopen Settings to retry.'))
  }, [web, supported])
  const save = (patch: Partial<typeof prefs>) => {
    updatePreferences(patch)
    if (web && enabled) void savePushPreferences().catch(() => setError('Could not save delivery preferences. Please try again.'))
  }
  const toggle = async () => {
    setBusy(true)
    setError('')
    try {
      if (enabled) await disablePush()
      else await enablePush()
      setEnabled(!enabled)
    } catch (error) {
      setError((error as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className={styles.card}>
      {web && (
        <div className={`${styles.row} ${styles.column}`}>
          <strong>Notifications on this device</strong>
          <p className={styles.sheetNote}>
            {supported
              ? 'Get replies and failures even when a-msg is closed. Notifications show a brief update; open the conversation to read it.'
              : 'On iPhone or iPad, use Safari → Share → Add to Home Screen, then open a-msg from its icon to enable notifications. Other browsers need HTTPS and Web Push support.'}
          </p>
          {supported && (
            <Button variant="primary" disabled={busy || enabled === null} onClick={() => void toggle()}>
              {busy ? 'Saving…' : enabled ? 'Turn off notifications' : 'Enable notifications'}
            </Button>
          )}
          {error && <p role="alert" className={styles.importError}>{error}</p>}
        </div>
      )}
      <Switch
        label={web ? 'Agent replies' : 'Completed background runs'}
        checked={prefs.notifyCompleted}
        onChange={(notifyCompleted) => save(web ? { notifyCompleted, notifyReplies: notifyCompleted } : { notifyCompleted })}
      />
      <Switch
        label="Failures that need attention"
        checked={prefs.notifyFailures}
        onChange={(notifyFailures) => save({ notifyFailures })}
      />
      {!web && (
        <Switch
          label="Replies while the window is inactive"
          checked={prefs.notifyReplies}
          onChange={(notifyReplies) => save({ notifyReplies })}
        />
      )}
    </div>
  )
}
