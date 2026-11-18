# Security

## Reporting a vulnerability

Report vulnerabilities privately through GitHub's **Report a vulnerability** button on this repository's Security tab.
Do not open a public issue. Include the version or commit, what an attacker needs, and the steps to reproduce.

## Security model

a-msg is for one owner. It has one account, and every credential it issues acts as that owner.

**A credential is shell access to the service host.** A paired device or signed-in browser can talk to Hermes, which
has a terminal tool, and can start Codex or Claude Code sessions at Full access. It can also create a coding-agent
connection on the service host with any program. Protect device credentials and the browser password and
authenticator secret as you would an SSH key to that host. Revoke lost devices under Settings, Connections.

A runner on another host is different. Its program, folders and highest access level are set on that host, and the
service cannot raise them.

What the service does to keep credentials to the owner:

- Device, connector and runner tokens are 256-bit random values, stored only as SHA-256 hashes and compared in
  constant time. Pairing codes have 58 bits, last ten minutes and work once.
- The service listens on loopback by default. Remote access goes through an SSH tunnel or a separate proxy listener.
- Browser access needs a password and a single-use TOTP code. Sign-in is rate limited per address and in total. The
  session cookie is `__Host-`, Secure, HttpOnly and SameSite=Strict, and state-changing requests must carry the
  configured Origin.
- On the proxy listener, only configured proxies may connect, the wrong `Host` is refused, and connector routes and
  API documents are hidden.
- Pages get a strict Content Security Policy. Files are always downloaded as sandboxed attachments, never rendered in
  the app's origin.
- Agent file attachments are limited to `AMSG_WORKSPACE_ROOTS`, after resolving symbolic links.
- Web Push goes only to Apple, Google and Mozilla push endpoints, without following redirects. Notification text
  (the reply, up to 400 characters) is encrypted end to end to the browser (RFC 8291).
- The desktop app keeps credentials in the macOS Keychain. Its window has no filesystem, shell or HTTP permissions,
  and it accepts `http://` only for loopback addresses.

Logs are JSON lines with credentials redacted and no message bodies.

## Running it safely

- Run the service as an ordinary user, not root. `scripts/install-server` refuses root.
- Keep the service on loopback. For browser access, put a reverse proxy with a valid certificate in front of the proxy
  listener and trust only that proxy (`amsg web --trust`).
- Keep `<data directory>` (default `~/.local/share/amsg`) private. It holds the database, uploaded files, the Web
  Push key and backups. The service creates it with mode 700.
- Back up with `amsg backup`. A restore revokes every device credential.
