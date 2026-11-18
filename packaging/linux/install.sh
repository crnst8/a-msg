#!/usr/bin/env bash
# Installs the a-msg service for this user, beside an existing Hermes. Only a-msg-owned files are written.
#   ./install.sh [--hermes-home PATH] [--prefix PATH] [--no-systemd]
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
prefix="$HOME/.local/opt/amsg" hermes_home="${HERMES_HOME:-$HOME/.hermes}" systemd=1
min_hermes="0.23.0"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    --hermes-home) hermes_home="$2"; shift 2 ;;
    --no-systemd) systemd=0; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
# The standard install keeps hermes in its own venv, which non-login shells do not have on PATH.
hermes_bin="$(command -v hermes || true)"
for candidate in "$hermes_home/hermes-agent/venv/bin/hermes" "$HOME/.local/bin/hermes"; do
  [[ -n "$hermes_bin" ]] && break
  [[ -x "$candidate" ]] && hermes_bin="$candidate"
done
[[ -n "$hermes_bin" ]] || { echo "Hermes was not found. Install Hermes first: a-msg runs beside it." >&2; exit 1; }
version="$("$hermes_bin" --version | head -1 | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | tr -d v || true)"
lowest="$(printf '%s\n%s\n' "$min_hermes" "$version" | sort -V | head -1)"
if [[ -z "$version" || "$lowest" != "$min_hermes" ]]; then
  echo "Hermes $min_hermes or newer is required; found '${version:-unknown}'. Update Hermes, then run this again." >&2
  exit 1
fi
[[ -d "$hermes_home" ]] || { echo "No Hermes profile at $hermes_home. Pass --hermes-home." >&2; exit 1; }
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="$hermes_home/.amsg-backup-$stamp"
mkdir -p "$backup"
[[ -d "$hermes_home/plugins/amsg" ]] && cp -R "$hermes_home/plugins/amsg" "$backup/plugin"
[[ -f "$hermes_home/.env" ]] && cp -p "$hermes_home/.env" "$backup/.env"
mkdir -p "$prefix" "$hermes_home/plugins" "$HOME/.local/bin"
rm -rf "$prefix/service" "$hermes_home/plugins/amsg"
cp -R "$here/service" "$prefix/service"
cp -R "$here/hermes-plugin" "$hermes_home/plugins/amsg"
ln -sfn "$prefix/service/amsg" "$HOME/.local/bin/amsg"
HERMES_HOME="$hermes_home" "$hermes_bin" plugins enable amsg >/dev/null
"$prefix/service/amsg" setup --hermes-env "$hermes_home/.env"
if [[ $systemd == 1 ]] && systemctl --user show-environment >/dev/null 2>&1; then
  unit="$HOME/.config/systemd/user/amsg.service"
  mkdir -p "$(dirname "$unit")"
  cat > "$unit" <<UNIT
[Unit]
Description=a-msg service
After=network-online.target

[Service]
ExecStart=$prefix/service/amsg serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now amsg.service
  echo "Started amsg.service. To keep it running after logout: sudo loginctl enable-linger $USER"
else
  echo "No user service manager here. Run in the foreground or under your supervisor: $prefix/service/amsg serve"
fi
echo "Next: restart the Hermes gateway so it loads the plugin, then pair the app with the code above."
