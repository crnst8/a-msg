# Compatibility record

Verified on Debian 13, x86_64, kernel 6.12. Updated 2026-11-06.

## Pinned toolchain

| Tool | Version | Pinned in |
|---|---|---|
| Node | 24 | `.nvmrc`, `package.json` |
| pnpm | 12.10.1 | `package.json` |
| TypeScript | 5.9.3 | `apps/desktop/package.json` |
| Python | 3.13 | `.python-version`, `pyproject.toml` |
| uv | 0.13.7 | CI, `packaging/docker/Dockerfile` |
| Rust | 1.99.1 | `rust-toolchain.toml` |
| Tauri | 2.12.5 | `Cargo.toml`, `Cargo.lock` |

TypeScript stays on 5.9 because `openapi-typescript` 7.13 requires `^5`.

## Hermes

Tested against **hermes-agent v0.23.0**, commit `2bd378ed1b8d`, installed from its lockfile in an isolated profile
(`scripts/hermes-dev`). The default `~/.hermes` was not touched.

Also run on Ubuntu 26.04 (x86_64) with the gateway under a user systemd service beside Slack. The standard Hermes
install keeps `hermes` in its own venv, off PATH for non-login shells; `install.sh` looks in
`~/.hermes/hermes-agent/venv/bin` as well.

| Capability | Status | Evidence |
|---|---|---|
| Third-party platform plugin | supported | `plugin.yaml`, `register(ctx)`; `hermes plugins validate` passes |
| `connect`, `disconnect`, `send`, `edit_message` | supported | Real gateway logs `amsg connected`; real replies |
| `handle_message`, `MessageEvent.channel_prompt` | supported | Turn context reaches the model without entering its transcript |
| Streaming | supported | Off by default in Hermes; needs `streaming.enabled: true`. Hermes streams by `send` then `edit_message`, about every 0.8 s. a-msg shows whole-body updates, not tokens, and removes the default cursor. A custom `streaming.cursor` is not removed |
| Cancellation | supported | `/stop` through `handle_message`; `Turn ended: interrupted_by_user`; confirmed by outcome `CANCELLED` |
| `send_document`, `send_image_file` | supported | File reached the reply card and Library |
| Tool registration | supported | Tools must be registered inside `register()`, as the bundled `a2a` plugin does |
| Session per chat ID | supported | One Hermes session per space; history survives gateway restart |
| Cron listing and changes | supported, internal | `tools.cronjob_tools.cronjob`. Hermes calls this module internal |
| Cron delivery to `amsg:<space id>` | supported | Real job delivered to its space |
| Out-of-process delivery | supported | `standalone_sender_fn` registered; the running gateway path is what was exercised |
| Native approval and clarify prompts | unsupported in v1 | Not implemented in the adapter |
| Token-level streaming | unknown | `send_draft` exists but was not used |

### Behaviour that differs from a first reading

- **Model provider.** Hermes has a native `opencode-go` provider (env `OPENCODE_GO_API_KEY`). It sends the
  `x-opencode-session` header the gateway now requires, so a custom endpoint would not work without that header.
- **`on_processing_complete(SUCCESS)` does not mean a reply exists.** An event received during startup restore is queued
  and reported as success with nothing sent, then replayed through `handle_message`. The adapter keeps the run open and
  fails it with `empty_reply` if no replay arrives within 60 seconds.
- **A tool `check_fn` is evaluated during warm-up, outside any session, and cached.** It must not read session state.
  Run binding is enforced when the tool is called.
- **`hermes plugins validate` warns about two private imports:** `tools.cronjob_tools.cronjob` and
  `gateway.platforms.send_targets.parse_nonempty`. No public interface exists for either. They may change between
  Hermes releases, so recheck them when moving the pin. The cron import is called only from a command handler, and a
  failure there is reported as a provider error.
- **Home channel.** Without `AMSG_HOME_CHANNEL`, Hermes prepends a notice to the first reply. `amsg setup` sets it to General.
- **Scheduled results** carry a header and footer unless `cron.wrap_response: false`.
- **Timezone.** `hermes_time.get_timezone_name()` is empty when neither the profile nor `HERMES_TIMEZONE` sets a zone, and
  jobs then run on the host clock. The adapter reports the host's IANA zone in that case.
- **Headless Linux.** The Tauri app needs a session bus: run it under `dbus-run-session`.

## Coding agents

| Agent | Protocol | Status |
|---|---|---|
| Codex CLI 0.166.0 | `codex exec --json` | Real session completed a fixture task, returned a file, was cancelled truthfully |
| Claude Code 2.1.318 | `claude -p --output-format stream-json` | Parser and arguments tested against recorded event shapes. No real session run |

Codex also ran for real through `amsg runner` from the packaged binary, on a ChatGPT login with no API key: a file
returned, a resumed turn, an attachment read, and Stop in 0.6 s with no process left. On that resumed turn a write
inside the folder succeeded and a write outside it failed with a read-only file system error.

`codex exec resume` has no `--sandbox` flag, so a-msg passes `-c sandbox_mode=...` on every resumed turn. Attachments are
written to `.amsg/inbox/<message id>/` in the session folder and named in the prompt.

Hermes tool use appears as the running reply's status through the `pre_tool_call` and `post_tool_call` plugin hooks, so
it needs no `display.tool_progress` setting and stays out of the reply text. Text comes from Hermes's redacted tool
preview and clears when the tool ends. Tested on real Hermes with a scripted local model, because the test key had
expired. Model reasoning and interim messages stay hidden, as `display.show_reasoning` and
`display.interim_assistant_messages` are off on `ag`.

Real Codex items observed: `thread.started`, `item.started` with `command_execution`, `item.completed` with
`agent_message`, `turn.completed`.

## Icons

The supplied colour mark is pale cream, too weak on a light Dock. Derived app icons place it on a `#1E1E1E` rounded tile,
which also matches the rail. Originals are unchanged. Tile, mark and `.icns` are in `apps/desktop/src-tauri/icons`,
generated by the pinned Tauri tooling and viewed at 16, 32, 128 and 256 px on light and dark backgrounds.

## Model

`deepseek-v4.1-flash` through OpenCode Go, for Hermes testing. It has the largest plan allowance of the listed models,
at $0.15–0.30 input and $0.60–1.20 output per million tokens. Tool calling worked in every real run. Runner-up:
`kimi-k2.7-code` at $0.95 and $4.00. Prices from `opencode.ai/docs/go`, read 2026-11-06.

## Adjustments to the plan

- **Browser harness.** The plan asks for a labelled mock transport. The harness uses the real API with the labelled
  "Test agent" provider instead. It tests more of the real path, and that provider refuses to start without `--dev`.
- **Added routes.** `POST /v1/spaces/{id}/read`, `/restore`, `POST /v1/runs/{id}/retry`,
  `POST /v1/operations/{id}/retry` and `/dismiss`, `GET /v1/artifacts/{id}`, `/v1/devices`, config patch routes and the
  `/v1/connector/*` routes.
- **Local cache.** The core caches snapshots and first message pages for offline reading, not full history.
