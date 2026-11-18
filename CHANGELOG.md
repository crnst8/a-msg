# Changelog

## 0.1.0 — 2026-11-18

- First public release.
- macOS desktop app (Tauri) for one Hermes agent and coding agents, with spaces, threads from a selection, live
  replies with tool activity, files both ways and a Library, search, and an offline outbox.
- Coding sessions with Codex or Claude Code on the Hermes host or on another machine through a runner, at Read only,
  Workspace or Full access.
- Hermes's scheduled jobs shown on the spaces they post to.
- Browser and phone access over HTTPS with a password and authenticator code, installable as a web app, with opt-in
  push notifications that show the reply and its space.
- `scripts/install-server` builds and installs or upgrades the service beside Hermes; `scripts/install-mac` builds
  and installs the Mac app. Release assets: Linux service archives (x86_64, aarch64) and an unsigned Apple silicon app.
