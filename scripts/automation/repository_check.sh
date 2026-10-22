#!/usr/bin/env bash

source "$(dirname "$0")/_common.sh"

case "${1:-}" in
  database-tests)
    require_env INKCRE_TEST_DATABASE_URL
    # This admitted suite needs migrated, disposable PostgreSQL, but no live AI or IMAP service.
    pdm run python -m pytest -q -rs \
      tests/agent/integration tests/ai/integration tests/deployment_config/integration \
      tests/info_base/integration tests/lexical_retrieval/integration \
      tests/organization/integration tests/peer/integration tests/semantic_retrieval/integration \
      tests/extensions/memos/integration tests/extensions/rss/integration/test_feed_collection.py \
      tests/migrations/test_memos_attachment_v2_migration.py
    ;;
  release-intent)
    arguments=()
    if [[ "${BASE_REVISION:-}" =~ ^[0-9a-f]{40}$ ]] &&
      [ "$BASE_REVISION" != 0000000000000000000000000000000000000000 ]; then
      arguments+=(--base "$BASE_REVISION")
    fi
    if [ "${RELEASE_PR:-false}" = true ]; then arguments+=(--release-pr); fi
    if [ "${MERGED_CHANGE:-false}" = true ]; then arguments+=(--merged); fi
    pdm run check:releases "${arguments[@]}"
    ;;
  extension-wheels)
    for extension in $(pdm run python scripts/release.py projects --extensions-only); do
      output="${RUNNER_TEMP:-/tmp}/wheels/$extension"
      mkdir --parents "$output"
      pdm run python -m build --wheel --no-isolation --outdir "$output" \
        "extensions/$extension"
      wheel="$(find "$output" -maxdepth 1 -type f -name '*.whl')"
      test "$(find "$output" -maxdepth 1 -type f -name '*.whl' | wc -l)" = 1
      pdm run python scripts/extension_distribution.py verify-wheel \
        --project "extensions/$extension" --wheel "$wheel"
    done
    ;;
  *) echo "usage: $0 database-tests|release-intent|extension-wheels" >&2; exit 2 ;;
esac
