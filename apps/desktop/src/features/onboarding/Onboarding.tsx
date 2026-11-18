import { useEffect, useState } from 'react'
import { Logo } from '@/components/Logo'
import { Symbol } from '@/components/Symbol'
import { Button, Field, IconButton, inputClass, monoClass } from '@/components/ui'
import { useApp } from '@/state/queries'
import type { ServiceError } from '@/transport/operations'
import type { SavedServer } from '@/transport/types'
import styles from './Onboarding.module.css'

const MESSAGES: Record<string, string> = {
  pairing_invalid: 'That code is wrong, used or expired. Run amsg pair for a new one.',
  rate_limited: 'Too many attempts. Wait a minute.',
  offline: 'No server answered at that address.',
  tls: 'The server certificate could not be verified.',
  incompatible: 'This server needs a different version of a-msg.',
  unauthenticated: 'This device is no longer paired with that server.',
  sign_in_invalid: 'The password or code is wrong.',
  code_used: 'That code was already used. Wait for the next one.',
  web_not_configured: 'Browser sign-in is not set up. Run amsg web on the server.',
  web_disabled: 'Browser sign-in is turned off on this server.',
  cross_origin: 'Open a-msg from its own address to sign in.',
}

export function Onboarding({ onPaired, revoked }: { onPaired: () => void; revoked: boolean }) {
  const { transport } = useApp()
  return transport.kind === 'web' ? (
    <SignIn onPaired={onPaired} revoked={revoked} />
  ) : (
    <Pairing onPaired={onPaired} revoked={revoked} />
  )
}

/** The browser build: one password and one authenticator code, then this browser stays signed in. */
function SignIn({ onPaired, revoked }: { onPaired: () => void; revoked: boolean }) {
  const { transport } = useApp()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(revoked ? 'Your session ended. Sign in again.' : null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (revoked) void transport.signOut()
  }, [transport, revoked])

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    transport
      .pair(location.origin, code.trim(), '', password)
      .then((info) => {
        if (!info.paired) throw Object.assign(new Error('The browser did not keep the session.'), { code: 'cookie' })
        onPaired()
      })
      .catch((failure: ServiceError) => {
        setError(MESSAGES[failure.code] ?? failure.message)
        setCode('')
        setBusy(false)
      })
  }

  return (
    <main className={styles.screen}>
      <div className={styles.card}>
        <Logo className={styles.mark} size={44} label="a-msg" />
        <h1 className={styles.title}>Sign in</h1>
        <form className={styles.form} onSubmit={submit}>
          {/* Lets password managers file the password and authenticator key under one login. */}
          <input type="text" name="username" autoComplete="username" value="amsg" readOnly hidden />
          <Field label="Password">
            <input
              className={`${inputClass} ${styles.input}`}
              type="password"
              name="password"
              autoComplete="current-password"
              autoFocus
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>
          <Field label="Authenticator code">
            <input
              className={`${inputClass} ${monoClass} ${styles.input}`}
              name="code"
              autoComplete="one-time-code"
              inputMode="numeric"
              pattern="[0-9 ]{6,7}"
              maxLength={7}
              required
              spellCheck={false}
              placeholder="000000"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </Field>
          {error && (
            <div className={styles.error} role="alert">
              <Symbol name="alert" size={18} />
              <span>{error}</span>
            </div>
          )}
          <Button className={styles.submit} variant="primary" type="submit" disabled={busy}>
            {busy ? 'Signing in' : 'Sign in'}
          </Button>
        </form>
      </div>
    </main>
  )
}

function Pairing({ onPaired, revoked }: { onPaired: () => void; revoked: boolean }) {
  const { transport } = useApp()
  const [endpoint, setEndpoint] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(revoked ? 'This device is no longer paired.' : null)
  const [busy, setBusy] = useState(false)
  const [local, setLocal] = useState(false)
  const [saved, setSaved] = useState<SavedServer[]>([])

  const fail = (failure: ServiceError) => {
    setError(MESSAGES[failure.code] ?? failure.message)
    setBusy(false)
  }

  useEffect(() => {
    void transport.localService().then((status) => setLocal(status.available))
    void (async () => {
      // A revoked credential is useless, so it goes now. The address stays for the new code.
      if (revoked) {
        const info = await transport.pairing()
        if (info.endpoint) setEndpoint(info.endpoint)
        await transport.signOut()
      }
      setSaved(await transport.savedServers())
    })()
  }, [transport, revoked])

  const startLocal = () => {
    setBusy(true)
    setError(null)
    transport
      .startLocalService()
      .then(onPaired)
      .catch((failure: ServiceError) => {
        setError(failure.message)
        setBusy(false)
      })
  }

  const pair = (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    transport
      .pair(endpoint.trim(), code.trim(), 'Mac')
      .then(onPaired)
      .catch(fail)
  }

  const reconnect = (server: SavedServer) => {
    setBusy(true)
    setError(null)
    transport
      .switchServer(server.service_id)
      .then(onPaired)
      .catch(async (failure: ServiceError) => {
        fail(failure)
        setSaved(await transport.savedServers())
      })
  }

  const forget = (server: SavedServer) => {
    void transport.signOut(server.service_id).then(async () => setSaved(await transport.savedServers()))
  }

  return (
    <main className={styles.screen}>
      <div className={styles.drag} data-tauri-drag-region />
      <div className={styles.card}>
        <Logo className={styles.mark} size={44} label="a-msg" />
        <h1 className={styles.title}>Connect your agent</h1>
        {saved.length > 0 && (
          <ul className={styles.saved} aria-label="Saved servers">
            {saved.map((server) => (
              <li key={server.service_id} className={styles.server}>
                <button type="button" className={styles.serverName} disabled={busy} onClick={() => reconnect(server)}>
                  {server.endpoint}
                </button>
                <IconButton icon="close" size={14} label={`Forget ${server.endpoint}`} disabled={busy} onClick={() => forget(server)} />
              </li>
            ))}
          </ul>
        )}
        <form className={styles.form} onSubmit={pair}>
          <Field label="Server address">
            <input
              className={`${inputClass} ${monoClass} ${styles.input}`}
              autoFocus={!saved.length}
              required
              spellCheck={false}
              placeholder="https://agent.example.net"
              value={endpoint}
              onChange={(event) => setEndpoint(event.target.value)}
            />
          </Field>
          <Field label="Pairing code">
            <input
              className={`${inputClass} ${monoClass} ${styles.input}`}
              required
              spellCheck={false}
              autoComplete="off"
              placeholder="XXXX-XXXX-XXXX"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </Field>
          {error && (
            <div className={styles.error} role="alert">
              <Symbol name="alert" size={18} />
              <span>{error}</span>
            </div>
          )}
          <Button className={styles.submit} variant="primary" type="submit" disabled={busy}>
            {busy ? 'Connecting' : 'Connect'}
          </Button>
          {local && (
            <Button variant="outline" disabled={busy} onClick={startLocal}>
              Use Hermes on this Mac
            </Button>
          )}
        </form>
      </div>
    </main>
  )
}
