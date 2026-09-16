#!/usr/bin/env bash
# Bring the LOCAL Hermes runtime checkout in step with the fork.
#
# Why this script exists
# ---------------------
# The Hermes runtime executes from ~/.hermes/hermes-agent. A fork that is
# current while that directory is stale buys nothing. VIGI-333 is the worked
# example: a genuine, correct merge landed and fixed nothing, because the
# thing actually running was a copy nobody had updated.
#
# So this script's job is not "git pull". It is to answer one question
# honestly: is the code that will run on the next restart the code we think
# we shipped? It refuses to guess, and it refuses to destroy anything to get
# to a clean answer.
#
# Usage:  scripts/hermes-local-sync.sh [--checkout-dir DIR] [--branch BRANCH]
# Exit:   0 = in step (and says whether a restart is needed)
#         1 = needs a human; nothing was changed

set -euo pipefail

CHECKOUT="${HERMES_CHECKOUT:-$HOME/.hermes/hermes-agent}"
BRANCH="custom"

while [ $# -gt 0 ]; do
  case "$1" in
    --checkout-dir) CHECKOUT="$2"; shift 2 ;;
    --branch)       BRANCH="$2";   shift 2 ;;
    -h|--help)      sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

die()  { echo "FAIL: $*" >&2; exit 1; }
note() { echo "  $*"; }

echo "Hermes local checkout sync"
echo "  checkout: $CHECKOUT"
echo "  branch:   $BRANCH"
echo

[ -d "$CHECKOUT/.git" ] || die "$CHECKOUT is not a git checkout."
cd "$CHECKOUT"

# ── 1. Never clobber local work. This is how the max_output_tokens override
#       spent weeks as 28 uncommitted lines in someone else's repository.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Uncommitted changes in the runtime checkout:"
  git status --short --untracked-files=no
  echo
  die "Refusing to touch this tree. Commit these onto a branch in the fork first,
      or capture them with 'git diff > /somewhere/safe.patch' and then discard them
      deliberately. This script will not stash and will not reset."
fi

# ── 2. A shallow clone cannot be reasoned about: unrelated commits look like
#       divergence because the common ancestor was never downloaded.
if [ -f .git/shallow ]; then
  note "shallow clone detected — converting to a full history (one time, slow)"
  git fetch --unshallow origin || die "could not unshallow; check network and remote"
fi

# ── 3. Remotes must point where we think they do.
origin_url="$(git remote get-url origin 2>/dev/null || echo '')"
case "$origin_url" in
  *silocyber/hermes-agent*) : ;;
  *) die "origin is '$origin_url', expected silocyber/hermes-agent.
      Run: git remote set-url origin https://github.com/silocyber/hermes-agent.git" ;;
esac
if git remote get-url upstream >/dev/null 2>&1; then
  push_url="$(git remote get-url --push upstream)"
  case "$push_url" in
    *NousResearch*) die "upstream still has a live push URL ($push_url).
      Run: git remote set-url --push upstream DISABLED_never_push_to_upstream" ;;
  esac
fi

# ── 4. Fetch and fast-forward. Fast-forward only: if the local branch has
#       diverged, that is a fact a human needs to see, not one to paper over.
git fetch origin --prune
git rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1 \
  || die "origin/$BRANCH does not exist in the fork."

before="$(git rev-parse HEAD)"
current_branch="$(git rev-parse --abbrev-ref HEAD)"

if [ "$current_branch" != "$BRANCH" ]; then
  note "switching from '$current_branch' to '$BRANCH'"
  git checkout -B "$BRANCH" --track "origin/$BRANCH" 2>/dev/null \
    || git checkout "$BRANCH" \
    || die "could not check out $BRANCH"
fi

git merge --ff-only "origin/$BRANCH" \
  || die "local $BRANCH has diverged from origin/$BRANCH and cannot fast-forward.
      Inspect with: git log --oneline --left-right origin/$BRANCH...$BRANCH
      Resolve deliberately. This script will not rebase or reset for you."

after="$(git rev-parse HEAD)"

# ── 5. Prove it, do not assume it. The tree must be byte-identical to the ref.
[ "$after" = "$(git rev-parse "origin/$BRANCH")" ] \
  || die "HEAD is $after but origin/$BRANCH is $(git rev-parse "origin/$BRANCH")"
[ -z "$(git status --porcelain --untracked-files=no)" ] \
  || die "the working tree differs from $BRANCH after the update"

echo
if [ "$before" = "$after" ]; then
  echo "Already in step at $(git rev-parse --short HEAD). No restart needed."
  exit 0
fi

echo "Updated $(git rev-parse --short "$before") -> $(git rev-parse --short "$after")"
git --no-pager log --oneline "$before..$after" | head -20
echo
echo "The running process is still on the OLD code. Restart Hermes to pick this up."
