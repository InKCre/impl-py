#!/usr/bin/env bash

source "$(dirname "$0")/_common.sh"

require_env IMAGE_TAG
pg_image="pgvector/pgvector:pg17@sha256:d2ef61f42ef767baa5a1475393303cc235bcd92febd9d7014eddb48b41f3bad0"

expect_reset_refusal() {
  local database_url="$1" confirmation="$2" detail="$3" result
  if result="$(docker run --rm --network host --env CORE_DATABASE_PASSWORD \
    --env "MIGRATION_DATABASE_URL=$database_url" --env POSTGREST_DATABASE_PASSWORD "$IMAGE_TAG" \
    python scripts/container.py db reset-dev --confirm "$confirmation" 2>&1)"; then
    echo "db reset-dev unexpectedly succeeded" >&2
    return 1
  fi
  jq -e --arg detail "$detail" \
    '.command == "reset-dev" and .error_type == "ValueError" and .detail == $detail' <<< "$result"
}

case "${1:-}" in
  build)
    require_env POSTGREST_IMAGE_TAG
    require_env SOURCE_REVISION
    docker build --build-arg "SOURCE_REVISION=$SOURCE_REVISION" --tag "$IMAGE_TAG" .
    docker build --build-arg "SOURCE_REVISION=$SOURCE_REVISION" \
      --file Dockerfile.postgrest --tag "$POSTGREST_IMAGE_TAG" .
    ;;
  inspect)
    docker run --rm --entrypoint python "$IMAGE_TAG" -c \
      "from pathlib import Path; required=('app','libs','utils','migrations/versions','alembic.ini','scripts/container.py','scripts/web.py','scripts/database.py','scripts/verify_postgrest_contract.py'); missing=[p for p in required if not Path(p).exists()]; assert not missing, missing; assert not Path('extensions').exists(); import os,pip,site; assert all(os.access(p,os.W_OK) for p in site.getsitepackages())"
    ;;
  initialize)
    for name in CORE_DATABASE_PASSWORD MIGRATION_DATABASE_URL POSTGREST_DATABASE_PASSWORD; do require_env "$name"; done
    for _ in 1 2; do
      docker run --rm --network host --env CORE_DATABASE_PASSWORD \
        --env MIGRATION_DATABASE_URL --env POSTGREST_DATABASE_PASSWORD "$IMAGE_TAG" \
        python scripts/container.py db init --profile development
    done
    ;;
  readiness)
    require_env MIGRATION_DATABASE_URL
    docker run --rm --network host --env MIGRATION_DATABASE_URL "$IMAGE_TAG" \
      python scripts/container.py db ready --profile development --json
    docker run --rm --network host --env MIGRATION_DATABASE_URL \
      --entrypoint alembic "$IMAGE_TAG" check
    ;;
  postgrest)
    for name in JWT_SECRET POSTGREST_DATABASE_PASSWORD POSTGREST_IMAGE_TAG; do
      require_env "$name"
    done
    postgrest_database_url="postgresql://authenticator:${POSTGREST_DATABASE_PASSWORD}@127.0.0.1:5432/inkcre"
    postgrest_id="$(docker run --detach --network host \
      --env "PGRST_DB_URI=$postgrest_database_url" --env PGRST_DB_SCHEMAS=inkcre \
      --env PGRST_DB_ANON_ROLE=anonymous \
      --env PGRST_DB_PRE_REQUEST=inkcre_internal.check_jwt \
      --env "PGRST_JWT_SECRET=$JWT_SECRET" --env PGRST_JWT_AUD=inkcre-api \
      --env PORT=13000 "$POSTGREST_IMAGE_TAG")"
    trap 'docker logs "$postgrest_id"; docker rm --force "$postgrest_id"' EXIT
    for _ in $(seq 1 30); do
      if docker run --rm --network host "$IMAGE_TAG" \
        python scripts/container.py db contract --json >/dev/null &&
        docker run --rm --network host --entrypoint python "$IMAGE_TAG" \
          scripts/verify_postgrest_contract.py --base-url http://127.0.0.1:13000 \
          --jwt-secret "$JWT_SECRET" \
          --wrong-jwt-secret ci-wrong-jwt-secret-at-least-32-bytes; then
        exit 0
      fi
      sleep 1
    done
    exit 1
    ;;
  reset)
    for name in CORE_DATABASE_PASSWORD MIGRATION_DATABASE_URL POSTGREST_DATABASE_PASSWORD; do require_env "$name"; done
    export PGDATABASE="${MIGRATION_DATABASE_URL/postgresql+psycopg:/postgresql:}"
    reset_sql() {
      docker run --rm --interactive --network host --env PGDATABASE "$pg_image" \
        sh -c 'exec psql --dbname="$PGDATABASE" -X --csv --tuples-only --set ON_ERROR_STOP=1 "$@"' sh "$@"
    }
    snapshot() {
      reset_sql < tests/development_reset_snapshot.sql | sha256sum
    }
    baseline="$(snapshot)"
    # Only seed the explicitly marked development database. A no-op reset must
    # leave a visible difference, even if init already created all the builtins.
    reset_sql --command "INSERT INTO inkcre.extensions (name)
      SELECT 'inkcre/reset-acceptance' FROM inkcre_internal.contract_state
      WHERE singleton AND environment = 'development'"
    seeded="$(snapshot)"
    test "$seeded" != "$baseline"
    expect_reset_refusal "$MIGRATION_DATABASE_URL" wrong-confirmation \
      "confirmation must be exactly reset-development-data"
    test "$(snapshot)" = "$seeded"
    for _ in 1 2; do
      docker run --rm --network host --env CORE_DATABASE_PASSWORD \
        --env MIGRATION_DATABASE_URL --env POSTGREST_DATABASE_PASSWORD "$IMAGE_TAG" \
        python scripts/container.py db reset-dev --confirm reset-development-data
      test "$(snapshot)" = "$baseline"
    done
    ;;
  web)
    for name in DATABASE_URL JWT_SECRET MIGRATION_DATABASE_URL; do require_env "$name"; done
    container_id="$(docker run --detach --network host --env DATABASE_URL \
      --env JWT_SECRET --env INKCRE_ENV_FILE --env MIGRATION_DATABASE_URL \
      --env PORT=18080 "$IMAGE_TAG" python scripts/container.py web)"
    trap 'docker logs "$container_id"; docker rm --force "$container_id"' EXIT
    for _ in $(seq 1 30); do
      curl --fail --silent http://127.0.0.1:18080/livez >/dev/null && break
      sleep 1
    done
    curl --fail --silent http://127.0.0.1:18080/livez
    # Async bootstrap can yield while liveness already responds; wait for readiness itself.
    curl --fail --silent --show-error --retry 30 --retry-delay 1 \
      --retry-max-time 30 --max-time 5 http://127.0.0.1:18080/readyz
    ;;
  export)
    for name in CORE_DATABASE_PASSWORD POSTGREST_DATABASE_PASSWORD SOURCE_REVISION; do require_env "$name"; done
    contract_directory="${RUNNER_TEMP:-/tmp}/database-contract"
    contract_database_url="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/inkcre_contract"
    mkdir --parents "$contract_directory"
    docker run --rm --network host --env PGPASSWORD=postgres "$pg_image" \
      createdb --host 127.0.0.1 --username postgres inkcre_contract
    docker run --rm --network host --env CORE_DATABASE_PASSWORD \
      --env "MIGRATION_DATABASE_URL=$contract_database_url" \
      --env POSTGREST_DATABASE_PASSWORD "$IMAGE_TAG" \
      python scripts/container.py db init --profile runtime
    docker run --rm "$IMAGE_TAG" python scripts/container.py db contract --json \
      > "$contract_directory/runtime-contract.json"
    docker run --rm --network host --env "MIGRATION_DATABASE_URL=$contract_database_url" \
      --entrypoint python "$IMAGE_TAG" scripts/export_database_roles.py \
      > "$contract_directory/database-roles.sql"
    docker run --rm --network host --env PGPASSWORD=postgres "$pg_image" pg_dump \
      --host 127.0.0.1 --username postgres --dbname inkcre_contract --schema-only \
      --no-owner --no-privileges --restrict-key "$SOURCE_REVISION" \
      > "$contract_directory/database-schema.sql"
    docker run --rm --network host --env PGPASSWORD=postgres "$pg_image" pg_dump \
      --host 127.0.0.1 --username postgres --dbname inkcre_contract --data-only \
      --table public.alembic_version --table inkcre_internal.contract_state \
      --inserts --no-owner --no-privileges --restrict-key "$SOURCE_REVISION" \
      >> "$contract_directory/database-schema.sql"
    python3 scripts/package_database_schema.py \
      --schema "$contract_directory/database-schema.sql" \
      --roles "$contract_directory/database-roles.sql" \
      --runtime-contract "$contract_directory/runtime-contract.json" \
      --output "$contract_directory/manifest.json" --source-revision "$SOURCE_REVISION"
    ;;
  restore)
    for name in CORE_DATABASE_PASSWORD POSTGREST_DATABASE_PASSWORD; do require_env "$name"; done
    contract_directory="${RUNNER_TEMP:-/tmp}/database-contract"
    restore_password=ci-restore-database-password-at-least-32-bytes
    restore_id="$(docker run --detach --env POSTGRES_DB=inkcre \
      --env "POSTGRES_PASSWORD=$restore_password" --env POSTGRES_USER=postgres \
      --publish 127.0.0.1::5432 "$pg_image")"
    trap 'docker logs "$restore_id"; docker rm --force "$restore_id"' EXIT
    for _ in $(seq 1 30); do
      if docker exec --env "PGPASSWORD=$restore_password" "$restore_id" psql \
        --host 127.0.0.1 --username postgres --dbname inkcre --tuples-only \
        --no-align --command 'SELECT 1' >/dev/null 2>&1; then break; fi
      sleep 1
    done
    restored_sql() {
      docker exec --interactive --env "PGPASSWORD=$restore_password" "$restore_id" \
        psql --host 127.0.0.1 --username postgres --dbname inkcre \
        -X --variable ON_ERROR_STOP=1 "$@"
    }
    restored_sql < "$contract_directory/database-roles.sql"
    restored_sql < "$contract_directory/database-schema.sql"
    restore_port="$(docker port "$restore_id" 5432/tcp | sed 's/.*://')"
    restore_database_url="postgresql+psycopg://postgres:${restore_password}@127.0.0.1:${restore_port}/inkcre"
    # Check the imported roles and schema before init can recreate either.
    docker run --rm --network host --env "MIGRATION_DATABASE_URL=$restore_database_url" \
      --entrypoint python "$IMAGE_TAG" scripts/export_database_roles.py |
      cmp - "$contract_directory/database-roles.sql"
    docker run --rm --network host --env "MIGRATION_DATABASE_URL=$restore_database_url" \
      --entrypoint alembic "$IMAGE_TAG" check
    revision="$(jq -er '.revision' "$contract_directory/runtime-contract.json")"
    restored_sql --set "expected_revision=$revision" < tests/restored_schema_data.sql
    docker run --rm --network host --env CORE_DATABASE_PASSWORD \
      --env "MIGRATION_DATABASE_URL=$restore_database_url" \
      --env POSTGREST_DATABASE_PASSWORD "$IMAGE_TAG" \
      python scripts/container.py db init --profile runtime
    docker run --rm --network host --env "MIGRATION_DATABASE_URL=$restore_database_url" \
      "$IMAGE_TAG" python scripts/container.py db ready --profile runtime --json
    restored_sql --command "INSERT INTO inkcre.extensions (name) VALUES ('inkcre/reset-acceptance')"
    expect_reset_refusal "$restore_database_url" reset-development-data \
      "operation requires a development database identity"
    test "$(restored_sql --tuples-only --no-align --command \
      "SELECT count(*) FROM inkcre.extensions WHERE name = 'inkcre/reset-acceptance'")" = 1
    ;;
  *) echo "unknown runtime contract command: ${1:-}" >&2; exit 2 ;;
esac
