#!/usr/bin/env bash
# Cuts a release. ./publish.sh calls this after writing the release notes; it can also run on its own.
#
#   scripts/release.sh 1.2.0
#
# In order, stopping at the first failure:
#   1. refuses unless on a clean main that is not behind origin, with CHANGELOG.md notes for this version
#   2. runs scripts/check --no-e2e
#   3. writes the version everywhere (scripts/set-version) and commits it, when it changed
#   4. tags v<version> and pushes main and the tag
#
# The tag starts .github/workflows/release.yml, which builds the Linux service archives and the macOS app and
# attaches them to the GitHub release for v<version>.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

version="${1:-}"
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || die "usage: scripts/release.sh 1.2.0"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git repository"
[[ "$(git rev-parse --abbrev-ref HEAD)" == main ]] || die "releases are cut from main"
[[ -z "$(git status --porcelain)" ]] || die "the working tree has changes. Commit or stash them first."
grep -q "^## $version " CHANGELOG.md 2>/dev/null || die "CHANGELOG.md has no '## $version — <date>' section"

step "Checking origin"
git fetch --tags --quiet origin
git rev-parse -q --verify "refs/tags/v$version" >/dev/null && die "tag v$version already exists"
if git rev-parse -q --verify origin/main >/dev/null; then
  [[ -z "$(git rev-list HEAD..origin/main)" ]] || die "main is behind origin/main. Pull first."
fi
# The public history carries no assistant attribution.
if git log --format=%B | grep -qiE 'noreply@anthropic\.com|generated with \[?claude'; then
  die "a commit message carries assistant attribution; rewrite it before releasing"
fi

step "Running the checks"
scripts/check --no-e2e

step "Setting version $version"
scripts/set-version "$version" >/dev/null
if [[ -n "$(git status --porcelain)" ]]; then
  git add -A
  git commit -q -m "release $version"
  echo "committed the version change"
else
  echo "already at $version"
fi

step "Tagging and pushing"
git tag -a "v$version" -m "v$version"
git push origin main
git push origin "v$version"

repo="$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
cat <<EOF

Released v$version. The build is running:
  https://github.com/$repo/actions/workflows/release.yml
Assets appear on https://github.com/$repo/releases/tag/v$version when it finishes.
EOF
