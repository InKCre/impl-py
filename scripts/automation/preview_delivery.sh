#!/usr/bin/env bash

source "$(dirname "$0")/_common.sh"

wait_for_release() {
  local app_name="$1" version="$2" status
  for _ in $(seq 1 120); do
    status="$(heroku releases --app "$app_name" --json |
      jq -r --argjson version "$version" '.[] | select(.version == $version) | .status')"
    case "$status" in
      succeeded) return 0 ;;
      failed) heroku releases:output "v$version" --app "$app_name"; return 1 ;;
    esac
    sleep 5
  done
  echo "Timed out waiting for $app_name release v$version" >&2
  return 1
}

retry_command() {
  local max_attempts="$1" attempt
  shift
  for attempt in $(seq 1 "$max_attempts"); do
    "$@" && return 0
    test "$attempt" -lt "$max_attempts"
    sleep "$((attempt * 2))"
  done
}

latest_release() {
  heroku releases --app "$1" --json | jq -r '.[0].version // empty'
}

wait_if_changed() {
  local app_name="$1" before="$2" after
  after="$(latest_release "$app_name")"
  if [ "$after" != "$before" ]; then
    wait_for_release "$app_name" "$after"
  fi
}

configure_core() {
  local before peer_id
  before="$(latest_release "$APP_NAME")"
  peer_id="$(python3 -c "import uuid; print(uuid.uuid5(uuid.NAMESPACE_URL, 'inkcre-core-py-pr-$PR_NUMBER'))")"
  heroku config:set --app "$APP_NAME" \
    DATABASE_SCALE_0=true "DATABASE_URL=$DATABASE_URL" \
    "EXTENSION_REGISTRY_URL=$EXTENSION_REGISTRY_URL" INKCRE_ENV_FILE= \
    "JWT_SECRET=$JWT_SECRET" OBSRV__LOGGING_BACKEND=postgresql "PEER_ID=$peer_id" \
    "PEER_NAME=core-py-pr-$PR_NUMBER" SKIP_EXTENSIONS_SYNC=0 >/dev/null
  heroku config:unset CLIENT_BASE_URL CLIENT_ID CLIENT_NAME LLM_SP_AK \
    LLM_SP_BASE_URL --app "$APP_NAME" >/dev/null || true
  if [ -n "$(heroku config:get MIGRATION_DATABASE_URL --app "$APP_NAME" || true)" ]; then
    heroku config:unset MIGRATION_DATABASE_URL --app "$APP_NAME" >/dev/null
  fi
  wait_if_changed "$APP_NAME" "$before"
}

configure_postgrest() {
  local before
  before="$(latest_release "$POSTGREST_APP_NAME")"
  heroku config:set --app "$POSTGREST_APP_NAME" \
    PGRST_DB_ANON_ROLE=anonymous PGRST_DB_POOL=2 \
    PGRST_DB_PRE_REQUEST=inkcre_internal.check_jwt PGRST_DB_SCHEMAS=inkcre \
    "PGRST_DB_URI=$POSTGREST_DATABASE_URL" PGRST_JWT_AUD=inkcre-api \
    "PGRST_JWT_SECRET=$JWT_SECRET" >/dev/null
  wait_if_changed "$POSTGREST_APP_NAME" "$before"
}

case "${1:-}" in
  resolve-neon)
    for name in NEON_API_KEY NEON_PROJECT_ID PR_NUMBER; do require_env "$name"; done
    branches_json="$(npx --yes neonctl@2.36.0 branches list \
      --project-id "$NEON_PROJECT_ID" --output json --no-analytics --no-color)"
    matches="$(jq -c --arg name "preview/core-py/pr-$PR_NUMBER" \
      '[.[] | select(.name == $name)]' <<<"$branches_json")"
    test "$(jq 'length' <<<"$matches")" = 1
    branch_id="$(jq -r '.[0].id' <<<"$matches")"
    test -n "$(jq -r '.[0].expires_at // empty' <<<"$matches")"
    runtime_database_url="$(npx --yes neonctl@2.36.0 connection-string "$branch_id" \
      --project-id "$NEON_PROJECT_ID" --role-name neondb_owner --pooled --no-color)"
    migration_database_url="$(npx --yes neonctl@2.36.0 connection-string "$branch_id" \
      --project-id "$NEON_PROJECT_ID" --role-name neondb_owner --no-pooled --no-color)"
    mask_value "$runtime_database_url"
    mask_value "$migration_database_url"
    emit_output branch_id "$branch_id"
    emit_output runtime_database_url "$runtime_database_url"
    emit_output migration_database_url "$migration_database_url"
    ;;
  normalize-roles)
    for name in BRANCH_ID HEAD_SHA MIGRATION_DATABASE_URL NEON_API_KEY NEON_PROJECT_ID; do
      require_env "$name"
    done
    environment="$(docker run --rm --env MIGRATION_DATABASE_URL --entrypoint python \
      "inkcre-preview-web:$HEAD_SHA" scripts/database_environment.py)"
    if [ "$environment" = preview ]; then exit 0; fi
    if [ "$environment" != runtime ] && [ "$environment" != absent ]; then
      echo "Refusing preview bootstrap from environment $environment" >&2
      exit 1
    fi
    roles_json="$(npx --yes neonctl@2.36.0 roles list --project-id "$NEON_PROJECT_ID" \
      --branch "$BRANCH_ID" --output json --no-analytics --no-color)"
    for role in authenticated anonymous authenticator inkcre_core; do
      if jq -e --arg role "$role" 'any(.[]; .name == $role)' <<<"$roles_json" >/dev/null; then
        npx --yes neonctl@2.36.0 roles delete "$role" --project-id "$NEON_PROJECT_ID" \
          --branch "$BRANCH_ID" --no-analytics --no-color
      fi
    done
    ;;
  converge-database)
    for name in CORE_DATABASE_PASSWORD HEAD_SHA MIGRATION_DATABASE_URL \
      POSTGREST_DATABASE_PASSWORD SOURCE_DATABASE_URL; do require_env "$name"; done
    readiness_file="$(mktemp)"
    trap 'rm -f "$readiness_file"' EXIT
    before_status=0
    docker run --rm --env MIGRATION_DATABASE_URL "inkcre-preview-web:$HEAD_SHA" \
      python scripts/container.py db ready --profile runtime --json > "$readiness_file" || before_status=$?
    emit_output database_before "$(jq -c --argjson probe_exit "$before_status" \
      '{probe_exit:$probe_exit,status,contract,migration,database}' "$readiness_file")"
    initialization_status=0
    docker run --rm --env CORE_DATABASE_PASSWORD --env MIGRATION_DATABASE_URL \
      --env POSTGREST_DATABASE_PASSWORD "inkcre-preview-web:$HEAD_SHA" \
      python scripts/container.py db init --profile runtime --environment preview || initialization_status=$?
    readiness_status=0
    docker run --rm --env MIGRATION_DATABASE_URL "inkcre-preview-web:$HEAD_SHA" \
      python scripts/container.py db ready --profile runtime --json > "$readiness_file" || readiness_status=$?
    emit_output database_after "$(jq -c --argjson probe_exit "$readiness_status" \
      '{probe_exit:$probe_exit,status,contract,migration,database}' "$readiness_file")"
    if [ "$initialization_status" != 0 ]; then
      echo "Preview database initialization failed; preserve this branch for inspection. Choose a forward-compatible candidate or a new PR/isolated branch from preview-base; do not reset automatically." >&2
      exit "$initialization_status"
    fi
    test "$readiness_status" = 0
    runtime_database_url="$(docker run --rm --env SOURCE_DATABASE_URL \
      --env TARGET_DATABASE_PASSWORD="$CORE_DATABASE_PASSWORD" \
      --env TARGET_DATABASE_ROLE=inkcre_core --entrypoint python \
      "inkcre-preview-web:$HEAD_SHA" scripts/rebind_database_url.py \
      --scheme postgresql+psycopg)"
    postgrest_database_url="$(docker run --rm --env SOURCE_DATABASE_URL \
      --env TARGET_DATABASE_PASSWORD="$POSTGREST_DATABASE_PASSWORD" \
      --env TARGET_DATABASE_ROLE=authenticator --entrypoint python \
      "inkcre-preview-web:$HEAD_SHA" scripts/rebind_database_url.py --scheme postgresql)"
    mask_value "$runtime_database_url"
    mask_value "$postgrest_database_url"
    emit_output runtime_database_url "$runtime_database_url"
    emit_output postgrest_database_url "$postgrest_database_url"
    ;;
  resolve-apps)
    require_env HEROKU_API_KEY
    require_env PR_NUMBER
    app_name="inkcre-core-py-pr-$PR_NUMBER"
    postgrest_app_name="inkcre-postgrest-pr-$PR_NUMBER"
    if ! app_json="$(heroku apps:info --app "$app_name" --json 2>/dev/null)"; then
      heroku apps:create "$app_name" --region us --stack container
      app_json="$(heroku apps:info --app "$app_name" --json)"
    fi
    test "$(jq -r '.app.stack.name' <<<"$app_json")" = container
    test "$(jq -r '.app.region.name' <<<"$app_json")" = us
    if ! postgrest_json="$(heroku apps:info --app "$postgrest_app_name" --json 2>/dev/null)"; then
      heroku apps:create "$postgrest_app_name" --region us --stack container
      postgrest_json="$(heroku apps:info --app "$postgrest_app_name" --json)"
    fi
    test "$(jq -r '.app.stack.name' <<<"$postgrest_json")" = container
    test "$(jq -r '.app.region.name' <<<"$postgrest_json")" = us
    pipeline_json="$(heroku pipelines:info inkcre-core --json)"
    for preview_app in "$app_name" "$postgrest_app_name"; do
      stage="$(jq -r --arg app "$preview_app" \
        '.apps[] | select(.name == $app) | .pipelineCoupling.stage // empty' \
        <<<"$pipeline_json")"
      if [ -z "$stage" ]; then
        heroku pipelines:add inkcre-core --app "$preview_app" --stage review
      else
        test "$stage" = review
      fi
    done
    deployed_release="$(heroku releases --app "$app_name" --json | jq -r \
      '[.[] | select(.status == "succeeded" and (.description | startswith("Deployed web")))] | first | .version // empty')"
    postgrest_release="$(heroku releases --app "$postgrest_app_name" --json | jq -r \
      '[.[] | select(.status == "succeeded" and (.description | startswith("Deployed web")))] | first | .version // empty')"
    emit_output app_name "$app_name"
    emit_output has_deployed_release "$([ -n "$deployed_release" ] && echo true || echo false)"
    emit_output has_postgrest_release "$([ -n "$postgrest_release" ] && echo true || echo false)"
    emit_output postgrest_app_name "$postgrest_app_name"
    emit_output postgrest_url "$(jq -r '.app.web_url' <<<"$postgrest_json")"
    emit_output web_url "$(jq -r '.app.web_url' <<<"$app_json")"
    ;;
  release)
    for name in APP_NAME DATABASE_URL EXTENSION_REGISTRY_URL HAS_POSTGREST_RELEASE \
      HEAD_SHA HEROKU_API_KEY JWT_SECRET PR_NUMBER POSTGREST_APP_NAME \
      POSTGREST_DATABASE_URL; do require_env "$name"; done
    mask_value "$JWT_SECRET"
    configure_core
    if [ "$HAS_POSTGREST_RELEASE" = false ]; then configure_postgrest; fi
    for attempt in $(seq 1 5); do
      if printf '%s' "$HEROKU_API_KEY" |
        docker login --username=_ --password-stdin registry.heroku.com; then break; fi
      test "$attempt" -lt 5
      sleep "$((attempt * 2))"
    done
    docker tag "inkcre-preview-web:$HEAD_SHA" "registry.heroku.com/$APP_NAME/web"
    docker tag "inkcre-preview-release:$HEAD_SHA" "registry.heroku.com/$APP_NAME/release"
    docker tag "inkcre-preview-postgrest:$HEAD_SHA" "registry.heroku.com/$POSTGREST_APP_NAME/web"
    retry_command 5 docker push "registry.heroku.com/$APP_NAME/web"
    retry_command 5 docker push "registry.heroku.com/$APP_NAME/release"
    retry_command 5 docker push "registry.heroku.com/$POSTGREST_APP_NAME/web"
    before="$(latest_release "$APP_NAME")"; heroku container:release release --app "$APP_NAME"; wait_if_changed "$APP_NAME" "$before"
    before="$(latest_release "$APP_NAME")"; heroku container:release web --app "$APP_NAME"; wait_if_changed "$APP_NAME" "$before"
    before="$(latest_release "$POSTGREST_APP_NAME")"; heroku container:release web --app "$POSTGREST_APP_NAME"; wait_if_changed "$POSTGREST_APP_NAME" "$before"
    if [ "$HAS_POSTGREST_RELEASE" = true ]; then configure_postgrest; fi
    heroku ps:scale web=1:eco --app "$APP_NAME"
    heroku ps:scale web=1:eco --app "$POSTGREST_APP_NAME"
    ;;
  advertise)
    for name in DATABASE_URL HEAD_SHA PR_NUMBER WEB_URL; do require_env "$name"; done
    peer_id="$(python3 -c "import uuid; print(uuid.uuid5(uuid.NAMESPACE_URL, 'inkcre-core-py-pr-$PR_NUMBER'))")"
    docker run --rm --env DATABASE_URL "inkcre-preview-release:$HEAD_SHA" \
      python scripts/configure_peer_runtime.py --peer-id "$peer_id" \
      --http-public-base-url "$WEB_URL"
    ;;
  probe)
    for name in APP_NAME HEAD_SHA HEROKU_API_KEY POSTGREST_APP_NAME POSTGREST_URL WEB_URL; do
      require_env "$name"
    done
    jwt_secret="$(heroku config:get JWT_SECRET --app "$APP_NAME")"
    test -n "$jwt_secret"
    mask_value "$jwt_secret"
    for _ in $(seq 1 60); do
      if curl --fail --silent "${WEB_URL}livez" >/dev/null &&
        curl --fail --silent "${WEB_URL}readyz" >/dev/null &&
        docker run --rm --entrypoint python "inkcre-preview-web:$HEAD_SHA" \
          scripts/verify_postgrest_contract.py --base-url "$POSTGREST_URL" \
          --jwt-secret "$jwt_secret" --wrong-jwt-secret "wrong-$jwt_secret"; then
        append_summary <<EOF
### Heroku preview

- App: \`$APP_NAME\`
- URL: $WEB_URL
- PostgREST app: $POSTGREST_APP_NAME
- PostgREST URL: $POSTGREST_URL
- Liveness: ready
- Readiness: ready
- PostgREST JWT read/write/deny contract: ready
EOF
        exit 0
      fi
      sleep 5
    done
    heroku logs --num 200 --app "$APP_NAME"
    heroku logs --num 200 --app "$POSTGREST_APP_NAME"
    exit 1
    ;;
  summarize)
    require_env HEAD_SHA
    require_env PR_NUMBER
    [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]
    append_summary <<EOF
### Preview 运行时分阶段结果

- 目标源码：\`$HEAD_SHA\`
- Neon 分支：\`${BRANCH_ID:-未取得}\`
- 配置的进程 Registry fallback：${EXTENSION_REGISTRY_URL:-未取得}；Peer／deployment override 仍可能优先，业务验收需核对实际选择。
- 数据库初始化：\`${DATABASE_RESULT:-未执行}\`
- 进程 release：\`${RELEASE_RESULT:-未执行}\`
- Peer 广告：\`${ADVERTISEMENT_RESULT:-未执行}\`
- 基础探测：\`${PROBE_RESULT:-未执行}\`

以下 contract.revision 是候选期望合同，migration.current 是实际读取的数据库 head；空值表示没有取得观察。

初始化前：
\`\`\`json
${DATABASE_BEFORE:-null}
\`\`\`
初始化后：
\`\`\`json
${DATABASE_AFTER:-null}
\`\`\`
EOF
    for app_name in "inkcre-core-py-pr-$PR_NUMBER" "inkcre-postgrest-pr-$PR_NUMBER"; do
      if releases="$(heroku releases --app "$app_name" --json 2>/dev/null)"; then
        observed="$(jq -c '.[0] | {version,status,description}' <<<"$releases")"
        printf -- '- 当前 Heroku release `%s`：`%s`（不单独证明源码或探测通过）\n' "$app_name" "$observed" | append_summary
      else
        printf -- '- 当前 Heroku release `%s`：未取得观察，不能推断服务不存在。\n' "$app_name" | append_summary
      fi
    done
    ;;
  *)
    echo "usage: $0 resolve-neon|normalize-roles|converge-database|resolve-apps|release|advertise|probe|summarize" >&2
    exit 2
    ;;
esac
