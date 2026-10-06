#!/usr/bin/env bash
# Propose merging the latest upstream Hermes release tag into intelio/pinned.
# Uses only the GitHub API (gh); no local merge, so conflicts are resolved in
# the PR itself. Env: REPO, UPSTREAM, PINNED_BRANCH, INPUT_TAG, DRY_RUN, GH_TOKEN.
set -euo pipefail

REPO="${REPO:-inteliodev/hermes-agent}"
UPSTREAM="${UPSTREAM:-NousResearch/hermes-agent}"
PINNED_BRANCH="${PINNED_BRANCH:-intelio/pinned}"
DRY_RUN="${DRY_RUN:-0}"

tag="${INPUT_TAG:-}"
if [[ -z "$tag" ]]; then
  tag="$(gh api "repos/$UPSTREAM/releases/latest" --jq .tag_name)"
fi
release_name="$(gh api "repos/$UPSTREAM/releases/tags/$tag" --jq .name 2>/dev/null || echo "$tag")"

# Resolve the tag to a commit SHA (dereference annotated tags).
read -r obj_sha obj_type < <(gh api "repos/$UPSTREAM/git/ref/tags/$tag" --jq '.object.sha + " " + .object.type')
if [[ "$obj_type" == "tag" ]]; then
  obj_sha="$(gh api "repos/$UPSTREAM/git/tags/$obj_sha" --jq .object.sha)"
fi
tag_sha="$obj_sha"
pinned_sha="$(gh api "repos/$REPO/git/ref/heads/$PINNED_BRANCH" --jq .object.sha)"
echo "upstream $tag -> $tag_sha ; $PINNED_BRANCH -> $pinned_sha"

# Already contained? compare BASE=tag ... HEAD=pinned: "ahead"/"identical" means pinned includes the tag.
status="$(gh api "repos/$REPO/compare/$tag_sha...$pinned_sha" --jq .status)"
echo "compare status (tag...pinned): $status"
if [[ "$status" == "ahead" || "$status" == "identical" ]]; then
  echo "$PINNED_BRANCH already contains $tag; nothing to do."
  exit 0
fi

branch="intelio/upstream-${tag}"
existing="$(gh pr list -R "$REPO" --base "$PINNED_BRANCH" --head "$branch" --state open --json url --jq '.[0].url // empty')"
if [[ -n "$existing" ]]; then
  echo "PR already open: $existing"
  exit 0
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY RUN: would create $branch at $tag_sha and open a PR into $PINNED_BRANCH."
  exit 0
fi

# Make sure the fork network has the release objects, then point a branch at the tag commit.
gh api -X POST "repos/$REPO/merge-upstream" -f branch=main >/dev/null 2>&1 \
  || echo "note: could not fast-forward fork main (continuing; objects are shared in the fork network)"
if gh api "repos/$REPO/git/ref/heads/$branch" >/dev/null 2>&1; then
  gh api -X PATCH "repos/$REPO/git/refs/heads/$branch" -f sha="$tag_sha" -F force=true >/dev/null
else
  gh api -X POST "repos/$REPO/git/refs" -f ref="refs/heads/$branch" -f sha="$tag_sha" >/dev/null
fi

body="$(cat <<MD
Automated weekly upstream sync.

- Upstream release: **${release_name}** (\`${tag}\`)
- Release commit: \`${tag_sha}\`
- Current \`${PINNED_BRANCH}\`: \`${pinned_sha}\`
- Upstream release notes: https://github.com/${UPSTREAM}/releases/tag/${tag}
- Diff: https://github.com/${REPO}/compare/${pinned_sha}...${tag_sha}

Merging this PR (use a **merge commit**, not squash/rebase, so upstream history stays intact) moves \`${PINNED_BRANCH}\` to include \`${tag}\`.

### Before merging
- [ ] Read the upstream release notes for breaking changes (auth store, profiles, gateway, API server, plugins).
- [ ] Confirm the alans-way plugin still loads against this version.

### After merging (on the VPS, never \`hermes update\`)
\`\`\`bash
cd ~/.hermes/hermes-agent
git fetch origin intelio/pinned
git checkout --detach <merge-commit-sha>   # pin by SHA
# reinstall deps per upstream release notes if required, then:
systemctl --user restart hermes-gateway.service
\`\`\`
Then verify: gateway active, no auth errors in \`journalctl --user -u hermes-gateway\`, Telegram bot answers, API server still bound to the tailnet IP only.
MD
)"

gh pr create -R "$REPO" --base "$PINNED_BRANCH" --head "$branch" \
  --title "Merge upstream ${tag} into ${PINNED_BRANCH}" --body "$body"
