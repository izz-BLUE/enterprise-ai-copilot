#!/bin/sh
set -eu

event_name="${1:-}"
before_sha="${2:-}"
current_sha="${3:-HEAD}"
base_sha="${4:-}"
head_sha="${5:-}"
zero_sha="0000000000000000000000000000000000000000"

commit_exists() {
  git cat-file -e "${1}^{commit}" 2>/dev/null
}

if [ "$event_name" = "pull_request" ] \
  && commit_exists "$base_sha" \
  && commit_exists "$head_sha"; then
  echo "${base_sha}..${head_sha}"
elif [ "$event_name" = "push" ] \
  && [ -n "$before_sha" ] \
  && [ "$before_sha" != "$zero_sha" ] \
  && commit_exists "$before_sha" \
  && commit_exists "$current_sha" \
  && git merge-base --is-ancestor "$before_sha" "$current_sha"; then
  echo "${before_sha}..${current_sha}"
else
  # New branches, non-fast-forward pushes, missing objects, and manual runs scan
  # all history reachable from the checked-out commit.
  echo "HEAD"
fi
