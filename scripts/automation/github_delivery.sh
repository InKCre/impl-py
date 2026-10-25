#!/usr/bin/env bash

source "$(dirname "$0")/_common.sh"

require_env GH_TOKEN
require_env GITHUB_REPOSITORY
require_env HEAD_SHA

if [[ ! "$HEAD_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "HEAD_SHA must be a lowercase 40-character Git SHA" >&2
  exit 1
fi

case "${1:-}" in
  production)
    main_sha="$(gh api "repos/$GITHUB_REPOSITORY/git/ref/heads/main" --jq '.object.sha')"
    test "$main_sha" = "$HEAD_SHA"
    exit 0
    ;;
  preview)
    require_env PR_NUMBER
    case "$PR_NUMBER" in
      ''|*[!0-9]*) echo "Invalid pull request number" >&2; exit 1 ;;
    esac
    pr_json="$(gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER")"
    test "$(jq -r '.head.repo.full_name' <<<"$pr_json")" = "$GITHUB_REPOSITORY"
    test "$(jq -r '.head.sha' <<<"$pr_json")" = "$HEAD_SHA"
    test "$(jq -r '.state' <<<"$pr_json")" = "open"
    required_checks=(
      "impl-py checks"
      "impl-py e2e tests"
      "Provision isolated branch"
    )
    ;;
  *)
    echo "usage: $0 production|preview" >&2
    exit 2
    ;;
esac

for _ in $(seq 1 120); do
  checks_json="$(
    gh api \
      -H "Accept: application/vnd.github+json" \
      "repos/$GITHUB_REPOSITORY/commits/$HEAD_SHA/check-runs?per_page=100"
  )"
  all_green=true
  for check_name in "${required_checks[@]}"; do
    conclusion="$(
      jq -r --arg name "$check_name" \
        '[.check_runs[] |
          select(.name == $name and .app.slug == "github-actions")] |
          last | .conclusion // ""' \
        <<<"$checks_json"
    )"
    if [ "$conclusion" != "success" ]; then
      all_green=false
    fi
  done
  if [ "$all_green" = true ]; then
    exit 0
  fi
  sleep 5
done

echo "Required checks did not become green for $HEAD_SHA" >&2
exit 1
