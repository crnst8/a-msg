#!/usr/bin/env bash
# Runs a coding agent on this host for an a-msg service on another host. The agent keeps its own login here.
#   ./install-runner.sh --ssh HOST --root PATH [--root PATH] [--agent codex|claude] [--name NAME]
#                       [--sandbox MODE] [--max-access read|workspace|full]
#                       [--local-port PORT] [--remote-port PORT] [--prefix PATH]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
prefix="$HOME/.local/opt/amsg" agent=codex name="" host="" sandbox="" max_access="" local_port=8711 remote_port=8710 roots=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ssh) host="$2"; shift 2 ;;
    --root) roots+=("$2"); shift 2 ;;
    --agent) agent="$2"; shift 2 ;;
    --name) name="$2"; shift 2 ;;
    --sandbox) sandbox="$2"; shift 2 ;;
    --max-access) max_access="$2"; shift 2 ;;
    --local-port) local_port="$2"; shift 2 ;;
    --remote-port) remote_port="$2"; shift 2 ;;
    --prefix) prefix="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$host" && ${#roots[@]} -gt 0 ]] || { echo "Pass --ssh HOST and at least one --root PATH." >&2; exit 2; }
[[ "$agent" == codex || "$agent" == claude ]] || { echo "--agent must be codex or claude." >&2; exit 2; }
[[ -z "$max_access" || "$max_access" =~ ^(read|workspace|full)$ ]] || { echo "--max-access must be read, workspace or full." >&2; exit 2; }
program="$(command -v "$agent" || true)"
[[ -n "$program" ]] || { echo "$agent was not found on this host." >&2; exit 1; }
if [[ "$agent" == codex ]] && ! "$program" login status >/dev/null 2>&1; then
  echo "Codex is not logged in here. Run: codex login" >&2; exit 1
fi
systemctl --user show-environment >/dev/null 2>&1 || { echo "This host has no user service manager." >&2; exit 1; }
command -v ssh >/dev/null || { echo "ssh was not found on this host." >&2; exit 1; }
name="${name:-${agent^} on $(hostname -s)}"
[[ "$agent" == claude ]] && name="${name/#Claude on/Claude Code on}"
token="$(ssh -o BatchMode=yes "$host" "\$HOME/.local/bin/amsg runner-token --name $(printf %q "$name")")"
[[ -n "$token" ]] || { echo "$host did not issue a credential." >&2; exit 1; }

mkdir -p "$prefix" "$HOME/.local/bin" "$HOME/.config/systemd/user" "$HOME/.config/amsg"
if [[ "$here/service" -ef "$prefix/service" ]]; then :; else
  rm -rf "$prefix/service"
  cp -R "$here/service" "$prefix/service"
fi
ln -sfn "$prefix/service/amsg" "$HOME/.local/bin/amsg"
env_file="$HOME/.config/amsg/runner.env"
( umask 077; printf 'AMSG_URL=http://127.0.0.1:%s\nAMSG_CONNECTOR_TOKEN=%s\n' "$local_port" "$token" > "$env_file" )
unset token

arguments=(runner --agent "$agent" --command "$program")
for root in "${roots[@]}"; do arguments+=(--root "$(realpath "$root")"); done
[[ -n "$sandbox" ]] && arguments+=(--sandbox "$sandbox")
[[ -n "$max_access" ]] && arguments+=(--max-access "$max_access")
exec_line="$prefix/service/amsg"
for argument in "${arguments[@]}"; do exec_line+=" \"$argument\""; done

cat > "$HOME/.config/systemd/user/amsg-tunnel.service" <<UNIT
[Unit]
Description=a-msg tunnel to $host
After=network-online.target

[Service]
ExecStart=$(command -v ssh) -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:$local_port:127.0.0.1:$remote_port $host
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
UNIT
cat > "$HOME/.config/systemd/user/amsg-runner.service" <<UNIT
[Unit]
Description=a-msg $agent runner
After=amsg-tunnel.service
Wants=amsg-tunnel.service

[Service]
EnvironmentFile=$env_file
Environment=PATH=$PATH
ExecStart=$exec_line
WorkingDirectory=%h
Restart=always
RestartSec=3
KillMode=mixed
TimeoutStopSec=15

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable amsg-tunnel.service amsg-runner.service >/dev/null 2>&1
systemctl --user restart amsg-tunnel.service amsg-runner.service
echo "Started the $agent runner. It appears in a-msg as \"$name\" under Code Connector."
echo "To keep it running after logout: sudo loginctl enable-linger $USER"
