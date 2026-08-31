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
  # 新分支、非 fast-forward 推送、缺少对象以及手动运行时，扫描从当前检出提交可达的全部历史。
  echo "HEAD"
fi
