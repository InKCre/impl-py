#!/usr/bin/env bash

source "$(dirname "$0")/_common.sh"

validate_preview_identity() {
  require_env PAGES_PROJECT
  require_env PR_NUMBER
  test "$PAGES_PROJECT" = inkcre-core-py-extension-registry-preview
  [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]
}

case "${1:-}" in
  build-extensions)
    validate_preview_identity
    preview_inputs="$GITHUB_WORKSPACE/.extension-preview-inputs"
    preview_registry="$GITHUB_WORKSPACE/.extension-preview-registry"
    public_origin="https://pr-$PR_NUMBER.$PAGES_PROJECT.pages.dev"
    pdm run build:extension-preview --output "$preview_inputs"
    pdm run inkcre-ext preview build --inventory "$preview_inputs/extensions.json" \
      --public-origin "$public_origin" --output "$preview_registry"
    ;;
  build-images)
    require_env HEAD_SHA
    require_env SOURCE_DIRECTORY
    docker build --platform linux/amd64 --provenance=false --target heroku-web \
      --build-arg "SOURCE_REVISION=$HEAD_SHA" \
      --tag "inkcre-preview-web:$HEAD_SHA" "$SOURCE_DIRECTORY"
    docker build --platform linux/amd64 --provenance=false --target heroku-release \
      --build-arg "SOURCE_REVISION=$HEAD_SHA" \
      --tag "inkcre-preview-release:$HEAD_SHA" "$SOURCE_DIRECTORY"
    docker build --platform linux/amd64 --provenance=false \
      --file "$SOURCE_DIRECTORY/Dockerfile.postgrest" \
      --build-arg "SOURCE_REVISION=$HEAD_SHA" \
      --tag "inkcre-preview-postgrest:$HEAD_SHA" "$SOURCE_DIRECTORY"
    ;;
  summarize)
    require_env HEAD_SHA
    append_summary <<EOF
### Preview 候选交付结果

| 层 | 本次结果 | 候选身份 |
| --- | --- | --- |
| 源码 | 精确 PR head | \`$HEAD_SHA\` |
| wheel Registry | \`${REGISTRY_RESULT:-未执行}\` | ${REGISTRY_SNAPSHOT:-未取得快照 URL} |
| Core／数据库／PostgREST 交付 | \`${DELIVERY_RESULT:-未执行}\` | 见下方分阶段结果或对应失败步骤 |

Registry 与运行时不是原子交付。Registry 成功而运行时失败时，不能将新的 Registry 当作整个候选已就绪；
旧 Core 或数据库可能仍存在，本流程没有自动回滚。基础探测也不代表 Web 已连接、扩展已启用或业务旅程通过。
EOF
    ;;
  validate-identity)
    validate_preview_identity
    ;;
  cleanup-apps)
    require_env HEROKU_API_KEY
    require_env PR_NUMBER
    [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]
    for app_name in "inkcre-core-py-pr-$PR_NUMBER" "inkcre-postgrest-pr-$PR_NUMBER"; do
      if heroku apps:info --app "$app_name" >/dev/null 2>&1; then
        heroku apps:destroy --app "$app_name" --confirm "$app_name"
      fi
    done
    ;;
  *) echo "usage: $0 build-extensions|build-images|summarize|validate-identity|cleanup-apps" >&2; exit 2 ;;
esac
