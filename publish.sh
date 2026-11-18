#!/usr/bin/env bash
# publish.sh: review, commit and release work made in this checkout.
#
#   ./publish.sh            review, commit, then release, asking at each step
#   ./publish.sh status     branch, version, last tag and working-tree state
#   ./publish.sh commit     review and commit, push main, no release
#   ./publish.sh release    release the current clean main
#   ./publish.sh --dry-run  review only; writes nothing
#
# A release writes the notes into CHANGELOG.md, then runs scripts/release.sh, which checks, sets the version, tags
# and pushes. The tag builds the release assets on GitHub (.github/workflows/release.yml); with gh signed in, this
# script waits for that build.
#
# Optional: .publish-guard (ignored by git) lists strings, one per line, that must never become public, such as
# your name, host names or private addresses. Commits and releases stop when one appears.

set -euo pipefail
cd "$(dirname "$0")"
PUBLIC_NAME="crnst8"
PUBLIC_EMAIL="dev@crnst8.com"
mode="all" dry_run=0
for arg in "$@"; do
  case "$arg" in
    status|commit|release) mode="$arg" ;;
    --dry-run) dry_run=1 ;;
    -h|--help) awk 'NR == 1 { next } /^# ?/ { sub(/^# ?/, ""); print; next } { exit }' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s (see --help)\n' "$arg" >&2; exit 2 ;;
  esac
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say()  { printf '%s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
ok()   { printf '   ✓ %s\n' "$*"; }
warn() { printf '   ! %s\n' "$*" >&2; }
die()  { printf '   ✗ %s\n' "$*" >&2; exit 1; }
heading() { printf '\n\033[1m%s\033[0m\n%s\n' "$*" "──────────────────────────────────────────────────────────────"; }
need_tty() { [[ -r /dev/tty && -w /dev/tty ]] || die "this step asks questions and needs a terminal"; }
yes_no() {
  local hint="[Y/n]" reply
  [[ "${2:-y}" == n ]] && hint="[y/N]"
  printf '\n   %s %s ' "$1" "$hint" >/dev/tty
  read -r reply </dev/tty || return 1
  reply="${reply:-${2:-y}}"
  [[ "$reply" =~ ^[Yy] ]]
}
ask() {
  local reply
  printf '\n   %s%s ' "$1" "${2:+ [$2]}" >/dev/tty
  read -r reply </dev/tty || return 1
  printf '%s' "${reply:-${2:-}}"
}
editor() { "${EDITOR:-$(command -v nano || command -v vi)}" "$@" </dev/tty >/dev/tty; }

current_version() { sed -n 's/^__version__ = "\(.*\)"/\1/p' services/api/src/amsg/__init__.py; }
last_tag() { git tag --list 'v*' --sort=-v:refname | head -n 1; }
bump() {
  local major minor patch
  IFS=. read -r major minor patch <<<"${1%%-*}"
  case "$2" in
    major) echo "$((major + 1)).0.0" ;;
    minor) echo "$major.$((minor + 1)).0" ;;
    patch) echo "$major.$minor.$((patch + 1))" ;;
  esac
}

# Strings from .publish-guard found in the given text (stdin). Prints the hits; returns 1 when there are any.
guard_patterns() { [[ -f .publish-guard ]] && grep -v -e '^#' -e '^[[:space:]]*$' .publish-guard; }
guard_text() {
  local patterns="$TMP/guard"
  guard_patterns > "$patterns" || return 0
  ! grep -n -i -F -f "$patterns" | head -20
}
guard_tree() {
  local patterns="$TMP/guard"
  guard_patterns > "$patterns" || return 0
  ! git grep -n -I -i -F -f "$patterns" "${1:-HEAD}" -- . | head -20
}

# Every line the pending commit would add: changed lines in tracked files, and whole untracked files.
added_text() {
  git diff HEAD | grep '^+' || true
  git ls-files --others --exclude-standard | while IFS= read -r file; do cat "$file" 2>/dev/null; done
}

assert_identity() {
  local email
  email="$(git var GIT_AUTHOR_IDENT | sed -n 's/.*<\(.*\)>.*/\1/p')"
  [[ "$email" == "$PUBLIC_EMAIL" ]] && return 0
  warn "this checkout would commit as $(git var GIT_AUTHOR_IDENT | sed 's/>.*/>/')"
  yes_no "Commit as $PUBLIC_NAME <$PUBLIC_EMAIL> in this checkout instead?" y || die "not committing to a public repo as $email"
  git config user.name "$PUBLIC_NAME"
  git config user.email "$PUBLIC_EMAIL"
  ok "identity set"
}

show_status() {
  heading "Status"
  say "   branch    $(git rev-parse --abbrev-ref HEAD)"
  say "   version   $(current_version)"
  local tag; tag="$(last_tag)"
  say "   last tag  ${tag:-none}"
  if [[ -n "$tag" ]]; then
    say "   since     $(git rev-list --count "$tag..HEAD") commit(s)"
  fi
  if git rev-parse -q --verify origin/main >/dev/null; then
    say "   unpushed  $(git rev-list --count origin/main..HEAD) commit(s)"
  fi
  if [[ -n "$(git status --porcelain)" ]]; then
    say "   changes:"
    git -c color.status=always status --short | sed 's/^/     /'
  else
    say "   working tree clean"
  fi
}

step_review() {
  heading "1. Review"
  if [[ -z "$(git status --porcelain)" ]]; then
    info "nothing to commit"
    return 1
  fi
  git -c color.status=always status --short | sed 's/^/   /'
  git diff HEAD --stat | sed 's/^/   /'
  if ! added_text | guard_text; then
    die "the changes contain a string from .publish-guard (lines above). Remove it, then run this again."
  fi
  ok "no guarded strings in the changes"
  if [[ $dry_run == 0 ]] && [[ -r /dev/tty ]] && yes_no "Open the full diff?" n; then
    git diff HEAD > "$TMP/diff"
    # shellcheck disable=SC2086  # PAGER may carry flags
    ${PAGER:-less -R} "$TMP/diff" </dev/tty >/dev/tty || true
  fi
  return 0
}

step_commit() {
  heading "2. Commit"
  need_tty
  assert_identity
  local kind summary message
  say "   1) feat  2) fix  3) chore  4) docs  5) refactor" >/dev/tty
  case "$(ask "Kind of change" 1)" in
    1|feat) kind=feat ;; 2|fix) kind=fix ;; 3|chore) kind=chore ;; 4|docs) kind=docs ;; 5|refactor) kind=refactor ;;
    *) die "pick 1 to 5" ;;
  esac
  summary="$(ask "One-line summary")"
  [[ -n "$summary" ]] || die "a summary is required"
  message="$kind: $summary"
  printf '%s\n' "$message" | guard_text || die "the commit message contains a guarded string"
  git add -A
  git commit -q -m "$message"
  ok "committed $(git rev-parse --short HEAD) $message"
  if yes_no "Push main to origin now?" y; then
    git push -q origin main && ok "pushed"
  fi
}

step_release() {
  heading "3. Release"
  need_tty
  [[ "$(git rev-parse --abbrev-ref HEAD)" == main ]] || die "releases are cut from main"
  [[ -z "$(git status --porcelain)" ]] || die "commit the changes first (./publish.sh commit)"
  assert_identity
  git fetch --tags --quiet origin 2>/dev/null || warn "could not reach origin"
  guard_tree HEAD || die "the tree contains a string from .publish-guard (lines above)"
  ok "no guarded strings in the tree"

  local tag cur version
  tag="$(last_tag)" cur="$(current_version)"
  if [[ -z "$tag" ]]; then
    info "no release yet; the first one is $cur"
    version="$(ask "Version" "$cur")"
  else
    info "last release $tag, $(git rev-list --count "$tag..HEAD") commit(s) since"
    say "   $(bump "$cur" patch) fixes only · $(bump "$cur" minor) new features · $(bump "$cur" major) breaking changes" >/dev/tty
    version="$(ask "Version" "$(bump "$cur" patch)")"
  fi
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || die "needs to look like 1.2.0"
  git rev-parse -q --verify "refs/tags/v$version" >/dev/null && die "v$version already exists"

  local notes="$TMP/notes.md"
  if grep -q "^## $version " CHANGELOG.md 2>/dev/null; then
    info "CHANGELOG.md already has notes for $version"
  else
    {
      printf '# Notes for %s. Lines starting with # are dropped. One "- " line per user-visible change.\n' "$version"
      printf '# Commits since %s:\n' "${tag:-the start}"
      git log --format='# %s' ${tag:+"$tag..HEAD"}
      git log --format='- %s' ${tag:+"$tag..HEAD"} | sed -E 's/^- (feat|fix|chore|docs|refactor): /- /'
    } > "$notes"
    editor "$notes"
    grep -v '^#' "$notes" | sed -e '/./,$!d' > "$notes.clean"
    [[ -s "$notes.clean" ]] || die "no notes written; nothing changed"
    guard_text < "$notes.clean" || die "the notes contain a guarded string"
    {
      [[ -f CHANGELOG.md ]] && sed -n '1,/^## /{/^## /!p;}' CHANGELOG.md || printf '# Changelog\n\n'
      printf '## %s — %s\n\n' "$version" "$(date +%Y-%m-%d)"
      cat "$notes.clean"
      printf '\n'
      [[ -f CHANGELOG.md ]] && sed -n '/^## /,$p' CHANGELOG.md
    } > "$TMP/CHANGELOG.md"
  fi

  heading "About to release v$version"
  say "   checks, version $cur → $version, tag v$version, push to origin"
  say "   then GitHub builds the Linux service archives and the macOS app and publishes the release"
  yes_no "Go? This pushes." y || { info "stopped; nothing written"; return 0; }
  if [[ -f "$TMP/CHANGELOG.md" ]]; then
    cp "$TMP/CHANGELOG.md" CHANGELOG.md
    git add CHANGELOG.md
    git commit -q -m "docs: release notes for $version"
    ok "CHANGELOG.md updated"
  fi
  scripts/release.sh "$version"

  if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
    heading "Waiting for the release build"
    local sha run=""
    sha="$(git rev-parse HEAD)"
    for _ in $(seq 1 30); do
      run="$(gh run list --workflow release.yml --commit "$sha" --limit 1 --json databaseId -q '.[0].databaseId' 2>/dev/null || true)"
      [[ -n "$run" ]] && break
      sleep 4
    done
    [[ -n "$run" ]] || { warn "the release run did not appear; check GitHub Actions"; return 0; }
    if gh run watch "$run" --exit-status --interval 15 >/dev/null; then
      ok "v$version published"
    else
      die "the release build failed: gh run view $run --log-failed"
    fi
  fi
}

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git repository"
case "$mode" in
  status) show_status ;;
  commit) step_review && { [[ $dry_run == 1 ]] || step_commit; } ;;
  release) step_release ;;
  all)
    show_status
    if step_review; then
      [[ $dry_run == 1 ]] && { info "dry run: stopping before the commit"; exit 0; }
      step_commit
    fi
    [[ $dry_run == 1 ]] && exit 0
    need_tty
    yes_no "Cut a release now?" n && step_release
    ;;
esac
exit 0
