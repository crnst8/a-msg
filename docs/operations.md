# Operations

Every command here was run against the built service on Linux unless marked otherwise.

## Install on the Hermes host

Hermes 0.23.0 or newer is required on the host. The a-msg service needs no Python of its own.

From a checkout, one command builds and installs it (see `scripts/install-server --help`):

```bash
scripts/install-server --hermes-home ~/.hermes
```

That builds the archive described below and runs its `install.sh`. To install on a host without the build tools,
build the archive on a machine of the same OS and architecture with `scripts/build-service --archive`, copy it over,
and run:

```bash
tar -xzf amsg-service-<version>-linux-x86_64.tar.gz
cd amsg-service-<version>-linux-x86_64
./install.sh --hermes-home ~/.hermes
```

The installer checks the Hermes version and refuses older ones, backs up the plugin folder and `.env` to
`<hermes home>/.amsg-backup-<time>`, installs under `~/.local/opt/amsg`, writes the connector credential into the
Hermes `.env`, and prints a pairing code. Where a user service manager exists it starts `amsg.service`. Without one it
prints the foreground command to run under your own supervisor. To keep the service running after logout, an
administrator runs `sudo loginctl enable-linger $USER`.

Then restart the Hermes gateway so it loads the plugin. To give the agent its a-msg tools, add this to the profile's
`config.yaml`, keeping any toolsets you already use:

```yaml
platform_toolsets:
  amsg: [amsg, cronjob, file, terminal]
cron:
  wrap_response: false    # scheduled results arrive without the Hermes header and footer
```

`hermes plugins enable` adds the `amsg` toolset to every saved platform list, and Hermes treats plugin toolsets as on
by default. On a host that also runs Slack or another platform, those sessions then get `list_spaces` and
`read_thread_context`, which read a-msg conversations. To keep the tools in a-msg, remove `amsg` from each other list
under `platform_toolsets`, add it to that platform's list under `known_plugin_toolsets`, and confirm with
`hermes tools list --platform slack`. `cron.wrap_response` is global, so leave it out if another platform relies on
the Hermes header and footer on scheduled results.

Replies arrive whole unless streaming is on. To stream them, set `streaming.enabled: true` in `config.yaml`. That
switch is global, so to keep another platform as it was, add `display.platforms.<name>.streaming: false` for it.

To change model for one space, send `/model <name>` in it.

Codex and Claude Code run on the service host unless you set up a runner. See Coding agents on another host.

To let the agent attach local files, set `AMSG_WORKSPACE_ROOTS` to a colon-separated folder list in the Hermes `.env`.

## Remote access

The service binds to loopback. Reach it through an SSH tunnel or a private HTTPS proxy:

```bash
ssh -N -L 8710:127.0.0.1:8710 your-host
```

The app accepts `https://` for remote services and `http://` for loopback only. It never disables certificate checks.

## Browser access

The service can serve the interface to a browser, including a phone, behind an HTTPS proxy. Sign-in takes one password
and a six-digit authenticator code (RFC 6238, as Bitwarden, 1Password and authenticator apps produce). The browser
then holds a device credential in a cookie and stays signed in for up to 90 days, or 30 days without use. Each
browser is listed under Settings, Connections, where it can be revoked like a paired device.

```bash
amsg web --origin https://agent.example.net --listen 127.0.0.1:8711 --trust 127.0.0.1
amsg web --password          # asks twice, then prints the authenticator secret and otpauth address
systemctl --user restart amsg.service
```

`scripts/install-server --web-origin https://agent.example.net` runs these three for a proxy on the same host. For a
proxy on another machine, listen on an address it can reach and trust only that proxy, for example
`--listen 192.0.2.10:8711 --trust 192.0.2.20`.

`--origin` is the public address. `--listen` adds a second listener for the proxy; the loopback listener stays as it
was for Hermes, runners and SSH tunnels. `--trust` names the proxy: only it may connect to the second listener, and
only its `X-Real-IP` or `X-Forwarded-For` is believed. Add the secret to an authenticator (in Bitwarden, the login's
Authenticator key). `--password-stdin` and `--totp-stdin` read the password and the secret from standard input for
scripted setup; a supplied secret is not printed back.

| Command | Effect |
|---|---|
| `amsg web` | Shows the current settings |
| `amsg web --password` | New password; sessions already signed in stay signed in |
| `amsg web --new-code` | New authenticator secret; codes from the old one stop working |
| `amsg web --sign-out-all` | Ends every browser session |
| `amsg web --off` | Turns browser access off (restart to apply) |

What the service enforces:

- The cookie is `__Host-amsg`: Secure, HttpOnly, SameSite=Strict, path `/`. Page scripts cannot read it.
- Requests that change anything must carry the configured Origin.
- Each authenticator code works once. A wrong password and a wrong code get the same reply.
- Five sign-in attempts per client address and 30 in total per 15 minutes; a success forgives that address.
- On the proxy listener: the wrong `Host` gets 421; connector routes and the API documents get 404.
- The page is served with a strict Content Security Policy (no inline script; the start-up mark's inline style is
  allowed by hash), `frame-ancestors 'none'`, `no-referrer` and `nosniff`. API responses are `no-store`.
  Files are always served as sandboxed attachments.

The proxy must send `Host` as the origin's host name and pass the event stream unbuffered (`/v1/events`, with a read
timeout above the 15-second heartbeat). Uploads go to `/v1/uploads` and can be up to the upload limit.

Caddy, which obtains the certificate itself and keeps `Host`:

```
agent.example.net {
	reverse_proxy 127.0.0.1:8711 {
		flush_interval -1
	}
}
```

nginx, with a certificate you already have:

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name agent.example.net;
    ssl_certificate     /etc/letsencrypt/live/agent.example.net/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/agent.example.net/privkey.pem;
    client_max_body_size 101m;

    location / {
        proxy_pass http://127.0.0.1:8711;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 1h;
    }
}
``` The interface
is the `web` folder beside the executable; `--root` points elsewhere. Restart the service after replacing it, so the
policy is computed from the new page.

On a host where a VPN accepts all of its traffic before the host firewall (Tailscale does), limit the second
listener to the proxy in a table that runs first, for example nftables at priority -10:

```
table inet amsg_web {
  chain input {
    type filter hook input priority -10; policy accept;
    tcp dport 8711 ip saddr != 192.0.2.20 drop
  }
}
```

## Coding agents on another host

A runner lets Hermes stay on one host while Codex or Claude Code works on another. It connects out to the service, so
the service needs no route to the runner's host. The agent uses its own login on that host, so no API key is stored in
a-msg. The program, sandbox and allowed folders are set on the runner's host. The service cannot change them.

The service host needs a build that includes `amsg runner-token`; see Upgrades. Archives are built per architecture, so
on a Raspberry Pi or another ARM host run `scripts/build-service --archive` there first. On the runner's host, log in to
the agent, extract that archive and run:

```bash
codex login            # once, on this host
./install-runner.sh --ssh your-hermes-host --root ~/projects
```

`--root` repeats. `--agent claude` runs Claude Code. `--name` sets the name shown in the app, by default
`Codex on <hostname>`. `--sandbox` takes `read-only`, `workspace-write` (the default) or `danger-full-access`. In
`workspace-write`, Codex can write only in the session folder, and its shell commands have no network access.

Each session has an access level, chosen in New session and changed later in the session's options menu:

| Access | Codex | Claude Code |
|---|---|---|
| Read only | `--sandbox read-only` | `--permission-mode plan` |
| Workspace | `--sandbox workspace-write` | `--permission-mode acceptEdits` |
| Full access | `--sandbox danger-full-access` | `--permission-mode bypassPermissions` |

A session may choose any level up to `--max-access`, which defaults to the level of `--sandbox`, so a plain install
offers Read only and Workspace. `--max-access full` lets sessions run with full access to the host and the network.
The runner checks every run against its own ceiling, so the service cannot raise it. A change applies from the next
message, including on resumed sessions. The header shows the level when it is not Workspace.

The installer needs key-based SSH to the service host. It writes the credential to `~/.config/amsg/runner.env` (mode
600) and starts two user services: `amsg-tunnel` for the SSH tunnel and `amsg-runner`. To keep them running after
logout, an administrator runs `sudo loginctl enable-linger $USER`.

The connection appears under Code Connector with no setup in the app. Use New session with a folder path on the runner's
host. Test connection reports the program, login and folders from that host. Running the installer again replaces the
credential. Logs are in `journalctl --user -u amsg-runner`, with no message bodies.

## Pair a device

Run `amsg pair` on the host, then enter the address and code in the app. Codes last ten minutes and work once.
Settings, Connections lists paired devices and revokes them.

Settings, Connections has two ways to leave a server. **Change server** keeps the credential in the Keychain and
lists the server on the connect screen, where one click returns to it. **Sign out** revokes this device on the
server and removes the credential, so reconnecting needs a new code. If the server cannot be reached, Sign out still
removes the credential here and says the server still lists the device; revoke it from another device. Removing a
server from the connect screen with its close button is the same as Sign out. Unsent messages stay on the device,
stored per server, and send when it returns.

## Back up and restore

```bash
amsg backup                                # writes to the backups folder in the data directory
amsg backup --output /path/to/new/folder
amsg restore /path/to/backup --data-dir /path/to/fresh/directory
```

A backup is a SQLite backup-API copy plus the file objects it references, with a checksum manifest. File removal
waits while a backup runs. Restore verifies message counts and every checksum, and refuses a directory that already
has a database.

After a restore:

1. Every device credential is revoked. Run `amsg pair` and pair each device again.
2. The service gets a new identity, so app caches and outboxes from the old one cannot mix with it.
3. Automations show as out of date until Hermes reports them. A copy of a host does not act as a second live
   destination until its connector is set up again.

## Upgrades

From a checkout, `git pull --ff-only && scripts/install-server` upgrades in place: it backs up the database to
`<data directory>/backups/pre-upgrade-<time>`, keeps the previous build as `service.prev-<time>`, restarts
`amsg.service` and restores the previous build if the new one does not start. When the Hermes plugin changed it
replaces it and prints the gateway restart command (or runs it with `--restart-hermes`). It does not issue a new
connector credential; `--reinstall` runs the full installer instead.

The service backs up the database before migrating. If a migration fails it restores that backup and stops with an
error naming the file. An older service refuses a newer database schema.

`install.sh` issues a new connector credential and runs `hermes plugins enable`, which adds the `amsg` toolset back to
every platform. To upgrade by hand from an archive, extract it and replace only what changed:

```bash
cp -R ~/.local/opt/amsg/service ~/.local/opt/amsg/service.prev
systemctl --user stop amsg.service
rm -rf ~/.local/opt/amsg/service && cp -R service ~/.local/opt/amsg/service
systemctl --user start amsg.service

# When the plugin changed, which also restarts every Hermes platform:
rm -rf ~/.hermes/plugins/amsg && cp -R hermes-plugin ~/.hermes/plugins/amsg
systemctl --user restart hermes-gateway.service
```

## Uninstall

```bash
./uninstall.sh --hermes-home ~/.hermes                 # keeps conversations and files
./uninstall.sh --hermes-home ~/.hermes --delete-data   # also deletes them
```

## Troubleshooting

Start with `amsg doctor`. Logs are JSON lines in `<data directory>/logs/service.log` with credentials redacted and no
message bodies.

| Symptom | Check |
|---|---|
| Header says Hermes offline | Is `hermes gateway run` running? Does its log show `amsg connected`? |
| Reply says "may or may not have received this" | The connector claimed the message and went silent. Retry sends it again |
| Reply fails with "The agent returned no reply" | Hermes finished with no output; see the Hermes log |
| Pairing code rejected | Codes expire after ten minutes and work once. Run `amsg pair` again |
| Automation marked out of date | The connector has not reported in ten minutes |
| Deletion shows Retry | The scheduler did not confirm every change. The space stays until it does |
| Agent cannot attach a file | The path must be under `AMSG_WORKSPACE_ROOTS` |
| Agent has no a-msg tools | Add `amsg` to `platform_toolsets.amsg` and restart the gateway |
| Browser sign-in says "not set up" | Run `amsg web --password` on the host |
| Browser gets 403 on the proxy listener | The proxy's address is not in `--trust` |
| Browser shows a blank page after an upgrade | Restart the service so it reads the new page |
| Runner shows Disconnected | On its host: `systemctl --user status amsg-tunnel amsg-runner` |
| Session fails with "does not exist on" | The folder must exist on the runner's host, not the service host |

## Limits

- One account per service. One API process.
- Two concurrent runs across all spaces, one per space.
- 100 MiB per file by default (`AMSG_UPLOAD_LIMIT`).
- Seven days of replay events. A client older than that reloads from a snapshot.
