#!/usr/bin/env bash

source "$(dirname "$0")/_common.sh"

case "${1:-}" in
  discover)
    projects="$(python3 scripts/release.py projects --extensions-only --json)"
    if [ "${EVENT_NAME:-}" = workflow_dispatch ]; then
      require_env REQUESTED_EXTENSION
      python3 scripts/release.py projects --extensions-only |
        grep --fixed-strings --line-regexp -- "$REQUESTED_EXTENSION"
    fi
    emit_output projects "$projects"
    ;;
  verify-main-history)
    git fetch --no-tags origin main
    git merge-base --is-ancestor HEAD origin/main
    ;;
  select)
    for name in EVENT_NAME EXTENSION; do require_env "$name"; done
    selected=false
    if [ "$EVENT_NAME" = workflow_dispatch ]; then
      require_env REQUESTED_EXTENSION
      test "${INITIAL_PUBLICATION:-}" = INITIAL_ONLY
      if [ "$EXTENSION" = "$REQUESTED_EXTENSION" ]; then selected=true; fi
    else
      require_env BEFORE_SHA
      before="$BEFORE_SHA"
      [[ "$before" =~ ^[0-9a-f]{40}$ ]]
      if [ "$before" = 0000000000000000000000000000000000000000 ]; then
        selected=true
      else
        git merge-base --is-ancestor "$before" HEAD
        selected="$(python3 scripts/release.py version-changed \
          --project "$EXTENSION" --base "$before")"
      fi
    fi
    emit_output selected "$selected"
    ;;
  build)
    for name in EXTENSION GITHUB_REPOSITORY GITHUB_RUN_ID GITHUB_SERVER_URL; do
      require_env "$name"
    done
    output="$(mktemp -d "${RUNNER_TEMP:-/tmp}/$EXTENSION.XXXXXX")"
    mkdir --parents "$output/raw" "$output/finalized"
    pdm run python -m build --wheel --no-isolation --outdir "$output/raw" \
      "extensions/$EXTENSION"
    wheel="$(find "$output/raw" -maxdepth 1 -type f -name '*.whl')"
    test -n "$wheel"
    test "$(find "$output/raw" -maxdepth 1 -type f -name '*.whl' | wc -l)" = 1
    # The runtime reads the Toolkit manifest inside .dist-info, not registry metadata.
    # Match the preview path before verification and upload; never publish the raw wheel.
    pdm run inkcre-ext python wheel finalize \
      --project "extensions/$EXTENSION/pyproject.toml" --wheel "$wheel" \
      --output-dir "$output/finalized"
    wheel="$(find "$output/finalized" -maxdepth 1 -type f -name '*.whl')"
    test "$(find "$output/finalized" -maxdepth 1 -type f -name '*.whl' | wc -l)" = 1
    metadata="${RUNNER_TEMP:-/tmp}/$EXTENSION-metadata.json"
    pdm run python scripts/extension_distribution.py verify-wheel \
      --project "extensions/$EXTENSION" --wheel "$wheel"
    pdm run python scripts/extension_distribution.py metadata \
      --project "extensions/$EXTENSION" \
      --source-repository "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" \
      --source-revision "$(git rev-parse HEAD)" --build-id "$GITHUB_RUN_ID" > "$metadata"
    emit_output metadata "$metadata"
    emit_output wheel "$wheel"
    ;;
  save-candidate)
    for name in CANDIDATE_ROOT EXTENSION GITHUB_RUN_ID GITHUB_SHA SELECTED; do
      require_env "$name"
    done
    mkdir -p "$CANDIDATE_ROOT"
    if [ -n "$(find "$CANDIDATE_ROOT" -mindepth 1 -print -quit)" ]; then
      echo "Candidate directory is not empty; refusing to replace saved release bytes." >&2
      exit 1
    fi
    if [ "$SELECTED" != true ]; then
      jq -n --arg extension "$EXTENSION" --arg run_id "$GITHUB_RUN_ID" \
        --arg source_revision "$GITHUB_SHA" \
        '{selected:false,extension:$extension,run_id:$run_id,source_revision:$source_revision}' \
        > "$CANDIDATE_ROOT/candidate.json"
      exit 0
    fi
    for name in METADATA WHEEL; do require_env "$name"; done
    test -f "$METADATA"
    test -f "$WHEEL"
    source_revision="$(jq -er '.prepare.python.source_revision' "$METADATA")"
    build_id="$(jq -er '.prepare.python.build_id' "$METADATA")"
    test "$source_revision" = "$GITHUB_SHA"
    test "$build_id" = "$GITHUB_RUN_ID"
    wheel_name="$(basename "$WHEEL")"
    cp "$METADATA" "$CANDIDATE_ROOT/metadata.json"
    cp "$WHEEL" "$CANDIDATE_ROOT/$wheel_name"
    wheel_sha256="$(shasum -a 256 "$CANDIDATE_ROOT/$wheel_name" | cut -d ' ' -f 1)"
    printf '%s\n' "$wheel_sha256" > "$CANDIDATE_ROOT/wheel.sha256"
    jq -n --arg extension "$EXTENSION" --arg run_id "$GITHUB_RUN_ID" \
      --arg source_revision "$source_revision" --arg wheel "$wheel_name" \
      --arg wheel_sha256 "$wheel_sha256" \
      '{selected:true,extension:$extension,run_id:$run_id,source_revision:$source_revision,wheel:$wheel,wheel_sha256:$wheel_sha256,metadata:"metadata.json"}' \
      > "$CANDIDATE_ROOT/candidate.json"
    ;;
  locate-artifact)
    for name in ARTIFACT_NAME GH_TOKEN GITHUB_REPOSITORY GITHUB_RUN_ID; do
      require_env "$name"
    done
    artifact_ids="$(gh api \
      --paginate \
      "/repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/artifacts" \
      --jq '.artifacts[] | select(.name == env.ARTIFACT_NAME and .expired == false) | .id')"
    count="$(printf '%s\n' "$artifact_ids" | sed '/^$/d' | wc -l | tr -d ' ')"
    if [ "$count" != 1 ]; then
      echo "Expected one unexpired saved candidate artifact named $ARTIFACT_NAME; found $count. Do not rebuild this release." >&2
      exit 1
    fi
    emit_output artifact_id "$artifact_ids"
    ;;
  restore-candidate)
    for name in CANDIDATE_ROOT EXTENSION GITHUB_RUN_ID GITHUB_SHA; do
      require_env "$name"
    done
    manifest="$CANDIDATE_ROOT/candidate.json"
    test -f "$manifest"
    test "$(jq -er '.extension' "$manifest")" = "$EXTENSION"
    test "$(jq -er '.run_id' "$manifest")" = "$GITHUB_RUN_ID"
    test "$(jq -er '.source_revision' "$manifest")" = "$GITHUB_SHA"
    selected="$(jq -er '
      if (.selected | type) == "boolean" then
        .selected | tostring
      else
        error("candidate selected must be a boolean")
      end
    ' "$manifest")"
    emit_output selected "$selected"
    if [ "$selected" != true ]; then exit 0; fi

    wheel_name="$(jq -er '.wheel' "$manifest")"
    metadata_name="$(jq -er '.metadata' "$manifest")"
    expected_sha256="$(jq -er '.wheel_sha256' "$manifest")"
    [[ "$wheel_name" =~ ^[A-Za-z0-9_.+-]+\.whl$ ]]
    [[ "$expected_sha256" =~ ^[0-9a-f]{64}$ ]]
    test -f "$CANDIDATE_ROOT/$wheel_name"
    test -f "$CANDIDATE_ROOT/$metadata_name"
    actual_sha256="$(shasum -a 256 "$CANDIDATE_ROOT/$wheel_name" | cut -d ' ' -f 1)"
    test "$actual_sha256" = "$expected_sha256"
    metadata_revision="$(jq -er '.prepare.python.source_revision' "$CANDIDATE_ROOT/$metadata_name")"
    metadata_build_id="$(jq -er '.prepare.python.build_id' "$CANDIDATE_ROOT/$metadata_name")"
    test "$metadata_revision" = "$GITHUB_SHA"
    test "$metadata_build_id" = "$GITHUB_RUN_ID"
    test "$(jq -er '.distribution' "$CANDIDATE_ROOT/$metadata_name")" = python
    emit_output wheel "$CANDIDATE_ROOT/$wheel_name"
    emit_output metadata "$CANDIDATE_ROOT/$metadata_name"
    ;;
  revalidate)
    require_env EXTENSION
    git fetch --no-tags origin main
    git merge-base --is-ancestor HEAD origin/main
    python3 scripts/release.py verify-artifact-unchanged \
      --project "$EXTENSION" --from HEAD --to origin/main
    ;;
  release-body)
    for name in EXTENSION METADATA; do require_env "$name"; done
    printf '<!-- inkcre-release\n'
    jq -ce --arg source_path "extensions/$EXTENSION" '
      .prepare.python | {schema_version:1, $source_path, host_sdk, host_sdk_version}
    ' "$METADATA"
    printf '%s\n' '-->'
    ;;
  publish)
    for name in GH_TOKEN GITHUB_REPOSITORY GITHUB_SHA EXTENSION METADATA WHEEL; do
      require_env "$name"
    done
    tag="$(jq -er '"inkcre-ext/" + .coordinate + "/python/v" + .version' "$METADATA")"
    encoded_tag="$(jq -rn --arg tag "$tag" '$tag | @uri')"
    existing_sha="$(gh api "repos/$GITHUB_REPOSITORY/git/matching-refs/tags/$encoded_tag" |
      jq -r --arg ref "refs/tags/$tag" '.[] | select(.ref == $ref) | .object.sha')"
    test -z "$existing_sha" || test "$existing_sha" = "$GITHUB_SHA"
    body="$(bash "$0" release-body)"
    wheel_name="$(basename "$WHEEL")"
    digest="sha256:$(shasum -a 256 "$WHEEL" | cut -d ' ' -f 1)"
    releases="repos/$GITHUB_REPOSITORY/releases"
    release="$(gh api "$releases?per_page=100" --paginate --slurp |
      jq -c --arg tag "$tag" '.[][] | select(.tag_name == $tag)')"
    if [ -z "$release" ]; then
      release="$(gh api --method POST "$releases" -f tag_name="$tag" \
        -f target_commitish="$GITHUB_SHA" -f name="$tag" -f body="$body" -F draft=true)"
    fi
    release_id="$(jq -er '.id' <<< "$release")"
    test "$(jq -er '.body' <<< "$release")" = "$body"
    if [ "$(jq -r '.draft' <<< "$release")" = true ]; then
      test "$(jq -er '.target_commitish' <<< "$release")" = "$GITHUB_SHA"
      if ! jq -e --arg name "$wheel_name" '.assets[] | select(.name == $name)' <<< "$release" >/dev/null; then
        asset_name="$(jq -rn --arg name "$wheel_name" '$name | @uri')"
        gh api --method POST \
          "https://uploads.github.com/repos/$GITHUB_REPOSITORY/releases/$release_id/assets?name=$asset_name" \
          -H 'Content-Type: application/octet-stream' --input "$WHEEL" >/dev/null
        release="$(gh api "$releases/$release_id")"
      fi
      jq -e --arg name "$wheel_name" --arg digest "$digest" '
        .assets[] | select(.name == $name and .digest == $digest)
      ' <<< "$release" >/dev/null
      gh api --method PATCH "$releases/$release_id" -F draft=false -f make_latest=false >/dev/null
    fi
    test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$encoded_tag" --jq '.object.sha')" = "$GITHUB_SHA"
    gh api "$releases/tags/$encoded_tag" |
      jq -e --arg name "$wheel_name" --arg digest "$digest" --arg body "$body" '
        .draft == false and .body == $body and
        any(.assets[]; .name == $name and .digest == $digest)
      '
    ;;
  *) echo "unknown Extension publication command: ${1:-}" >&2; exit 2 ;;
esac
