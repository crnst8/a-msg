# shellcheck shell=bash disable=SC2154  # $root is set by the script that sources this file.
# Shared by scripts/install-mac and scripts/install-server. Source it; do not run it.
# Finds or installs the build tools this repository pins. Tools it installs go under $AMSG_TOOLS
# (default ~/.local/share/amsg-build), except uv (~/.local/bin) and rustup (~/.cargo), which use their own installers.
# Nothing here needs sudo or edits shell profiles.

AMSG_TOOLS="${AMSG_TOOLS:-$HOME/.local/share/amsg-build}"
export PATH="$AMSG_TOOLS/bin:$AMSG_TOOLS/node/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0

say() { printf '\n== %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "$1 is required. $2"; }

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }

ensure_uv() {
  local version
  # The version CI pins.
  version="$(grep -oE 'version: "[0-9.]+"' "$root/.github/workflows/check.yml" 2>/dev/null | grep -oE '[0-9.]+' | head -1 || true)"
  if command -v uv >/dev/null; then return; fi
  say "Installing uv ${version:-latest} into ~/.local/bin"
  curl --proto '=https' --tlsv1.2 -LsSf "https://astral.sh/uv/${version:+$version/}install.sh" | env UV_NO_MODIFY_PATH=1 sh
  command -v uv >/dev/null || die "uv did not install."
}

node_major() { node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }

ensure_node() {
  local want os arch base file
  want="$(tr -dc '0-9' < "$root/.nvmrc")"
  if command -v node >/dev/null && [[ "$(node_major)" == "$want" ]]; then return; fi
  case "$(uname -s)" in Darwin) os=darwin ;; Linux) os=linux ;; *) die "Unsupported OS: $(uname -s)" ;; esac
  case "$(uname -m)" in arm64|aarch64) arch=arm64 ;; x86_64|amd64) arch=x64 ;; *) die "Unsupported CPU: $(uname -m)" ;; esac
  base="https://nodejs.org/dist/latest-v${want}.x"
  say "Installing Node ${want} (${os}-${arch}) into $AMSG_TOOLS/node"
  local tmp; tmp="$(mktemp -d)"
  curl --proto '=https' --tlsv1.2 -fsSL "$base/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
  file="$(grep -oE "node-v${want}\.[0-9]+\.[0-9]+-${os}-${arch}\.tar\.gz" "$tmp/SHASUMS256.txt" | head -1)"
  [[ -n "$file" ]] || die "No Node ${want} build for ${os}-${arch}."
  curl --proto '=https' --tlsv1.2 -fsSL "$base/$file" -o "$tmp/$file"
  (cd "$tmp" && grep " $file\$" SHASUMS256.txt | sha256 -c - >/dev/null) || die "Node download failed its checksum."
  rm -rf "$AMSG_TOOLS/node" && mkdir -p "$AMSG_TOOLS/node"
  tar -xzf "$tmp/$file" -C "$AMSG_TOOLS/node" --strip-components 1
  rm -rf "$tmp"
  hash -r
  [[ "$(node_major)" == "$want" ]] || die "Node ${want} is not first on PATH."
}

ensure_pnpm() {
  local want
  want="$(node -p "require('$root/package.json').packageManager.split('@')[1]")"
  if command -v pnpm >/dev/null && [[ "$(pnpm --version 2>/dev/null)" == "$want" ]]; then return; fi
  say "Installing pnpm ${want} into $AMSG_TOOLS/bin"
  mkdir -p "$AMSG_TOOLS/bin"
  if command -v corepack >/dev/null; then
    corepack enable --install-directory "$AMSG_TOOLS/bin" pnpm
  else
    npm install --global --prefix "$AMSG_TOOLS" --silent "pnpm@${want}"
  fi
  hash -r
  (cd "$root" && pnpm --version >/dev/null) || die "pnpm did not install."
}

ensure_rust() {
  if ! command -v rustup >/dev/null; then
    say "Installing rustup into ~/.cargo (the pinned toolchain follows from rust-toolchain.toml)"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs |
      sh -s -- -y --no-modify-path --profile minimal --default-toolchain none
  fi
  (cd "$root" && rustup toolchain install >/dev/null) || die "rustup could not install the pinned toolchain."
  (cd "$root" && cargo --version >/dev/null) || die "cargo is not available."
}

install_dependencies() {
  say "Installing dependencies from the lockfiles"
  (cd "$root" && pnpm install --frozen-lockfile)
  (cd "$root/services/api" && uv sync --frozen)
}
