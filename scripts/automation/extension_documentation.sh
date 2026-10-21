#!/usr/bin/env bash

source "$(dirname "$0")/_common.sh"

extension_coordinate() {
  python3 - "$EXTENSION" <<'PY'
import sys
import tomllib
from pathlib import Path

project = Path("extensions") / sys.argv[1] / "pyproject.toml"
data = tomllib.loads(project.read_text())
print(data["tool"]["inkcre-extension"]["name"])
PY
}

case "${1:-}" in
  prepare)
    for name in EXTENSION CANDIDATE_ROOT GITHUB_REPOSITORY GITHUB_RUN_ID \
      GITHUB_SERVER_URL GITHUB_SHA INKCRE_EXTENSION_REGISTRY_URL \
      NATIVE_DISTRIBUTION RELEASE_VERSION; do
      require_env "$name"
    done
    test ! -e "$CANDIDATE_ROOT"
    coordinate="$(extension_coordinate)"
    version="$RELEASE_VERSION"
    docs_root="extensions/$EXTENSION/docs"
    scopes=()
    while IFS= read -r scope; do scopes+=("$scope"); done < <(
      find "$docs_root" -mindepth 3 -maxdepth 3 -type f \
        -path '*/.vitepress/config.mts' -print |
        sed -E 's#/.vitepress/config\.mts$##; s#^.*/##' |
        sort
    )
    case "$NATIVE_DISTRIBUTION" in
      python) ;;
      module_federation) scopes=(global) ;;
      *) echo "Unsupported native Distribution: $NATIVE_DISTRIBUTION" >&2; exit 2 ;;
    esac
    test "${#scopes[@]}" -gt 0
    for scope in "${scopes[@]}"; do test -f "$docs_root/$scope/.vitepress/config.mts"; done

    mkdir -p "$CANDIDATE_ROOT"
    printf '%s\n' "${scopes[@]}" > "$CANDIDATE_ROOT/scopes.txt"
    pdm run inkcre-ext docs show \
      --registry-url "$INKCRE_EXTENSION_REGISTRY_URL" \
      --name "$coordinate" --version "$version" \
      --distribution "$NATIVE_DISTRIBUTION" > "$CANDIDATE_ROOT/current.json"

    for scope in "${scopes[@]}"; do
      site="$docs_root/$scope"
      INKCRE_DOCS_VERSION="$version" \
        pnpm --dir docs/_shared/website --ignore-workspace exec vitepress build "$PWD/$site"
      args=(
        --site "$site/.vitepress/dist"
        --output "$CANDIDATE_ROOT/$scope"
        --name "$coordinate"
        --version "$version"
        --scope "$scope"
        --distribution "$NATIVE_DISTRIBUTION"
        --source-repository "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY"
        --source-revision "$GITHUB_SHA"
        --build-id "$GITHUB_RUN_ID"
      )
      etag="$(jq -r --arg scope "$scope" \
        'first(.sets[] | select(.scope == $scope) | .etag) // empty' \
        "$CANDIDATE_ROOT/current.json")"
      if [ -n "$etag" ]; then args+=(--if-match "$etag"); fi
      pdm run inkcre-ext docs pack "${args[@]}"
    done
    ;;
  revalidate)
    require_env EXTENSION
    git fetch --no-tags origin main
    git merge-base --is-ancestor HEAD origin/main
    git diff --quiet HEAD..origin/main -- \
      "extensions/$EXTENSION/docs" docs/_shared pyproject.toml pdm.lock \
      scripts/automation/extension_documentation.sh \
      .github/workflows/extension-documentation-publish.yml
    ;;
  publish)
    for name in CANDIDATE_ROOT INKCRE_EXTENSION_REGISTRY_TOKEN \
      INKCRE_EXTENSION_REGISTRY_URL; do
      require_env "$name"
    done
    while IFS= read -r scope; do
      pdm run inkcre-ext docs publish \
        --registry-url "$INKCRE_EXTENSION_REGISTRY_URL" \
        --candidate "$CANDIDATE_ROOT/$scope" |
        tee "$CANDIDATE_ROOT/$scope/receipt.json"
    done < "$CANDIDATE_ROOT/scopes.txt"
    ;;
  verify)
    for name in CANDIDATE_ROOT GITHUB_RUN_ID GITHUB_SHA \
      INKCRE_EXTENSION_REGISTRY_URL; do
      require_env "$name"
    done
    first_scope="$(head -n 1 "$CANDIDATE_ROOT/scopes.txt")"
    coordinate="$(jq -r '.name' "$CANDIDATE_ROOT/$first_scope/candidate.json")"
    version="$(jq -r '.version' "$CANDIDATE_ROOT/$first_scope/candidate.json")"
    distribution="$(jq -er '.distribution' "$CANDIDATE_ROOT/$first_scope/candidate.json")"
    current="$(pdm run inkcre-ext docs show \
      --registry-url "$INKCRE_EXTENSION_REGISTRY_URL" \
      --name "$coordinate" --version "$version" \
      --distribution "$distribution")"
    while IFS= read -r scope; do
      record="$(jq -ce --arg scope "$scope" --arg revision "$GITHUB_SHA" \
        --arg build "$GITHUB_RUN_ID" \
        '.sets[] | select(.scope == $scope and .source_revision == $revision and
          .build_id == $build)' <<< "$current")"
      snapshot_url="$(jq -r '.snapshot_url' <<< "$record")"
      [[ "$snapshot_url" =~ ^https://registry-docs-[0-9a-f]{32}\.inkcre\.dev/$ ]]
      curl --fail --silent --show-error "$snapshot_url" > /dev/null
    done < "$CANDIDATE_ROOT/scopes.txt"
    ;;
  *) echo "unknown Extension documentation command: ${1:-}" >&2; exit 2 ;;
esac
