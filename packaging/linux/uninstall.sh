#!/usr/bin/env bash
# Removes a-msg binaries, the plugin and startup registration. Conversation data stays unless named.
#   ./uninstall.sh [--hermes-home PATH] [--prefix PATH] [--delete-data]
set -euo pipefail
prefix="$HOME/.local/opt/amsg" hermes_home="${HERMES_HOME:-$HOME/.hermes}" delete_data=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    --hermes-home) hermes_home="$2"; shift 2 ;;
    --delete-data) delete_data=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
if systemctl --user show-environment >/dev/null 2>&1; then
  systemctl --user disable --now amsg.service amsg-runner.service amsg-tunnel.service 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/amsg.service" "$HOME/.config/systemd/user/amsg-runner.service" \
    "$HOME/.config/systemd/user/amsg-tunnel.service"
  systemctl --user daemon-reload
fi
rm -f "$HOME/.local/bin/amsg" "$HOME/.config/amsg/runner.env"
rm -rf "$prefix" "$hermes_home/plugins/amsg"
data="${XDG_DATA_HOME:-$HOME/.local/share}/amsg"
if [[ $delete_data == 1 ]]; then
  rm -rf "$data"
  echo "Removed a-msg and its data."
else
  echo "Removed a-msg. Conversations remain in $data. Remove them with --delete-data."
fi
