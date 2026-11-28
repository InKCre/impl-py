# Docker

## Artifact Contract

The multi-stage `Dockerfile` builds one canonical provider-neutral service image plus two
operational helpers:

- `service`: the canonical core code image published to GHCR and transferred unchanged to Heroku;
- `artifact` and `heroku-web`: compatibility aliases of `service` for local and preview builds;
- `heroku-release`: a lightweight no-op release guard containing no core code;
- `Dockerfile.postgrest`: the separately pinned PostgREST transport.

- Python and PDM versions are pinned by `.python-version` and the Docker build argument
- production dependencies come only from the frozen root `pdm.lock`
- the final image contains Core code, shared libraries, prompts, Alembic configuration, every
  migration revision, and a writable Core virtual environment with native `pip`
- checked-in Extension source, custom ZIP bundles, catalogs, and Extension-local environments
  are not image inputs
- the final process runs as the non-root `inkcre` user

The service has no entry point and defaults to `python scripts/container.py web`, which keeps
Heroku's shell-based `CMD` behavior explicit. Call other supported commands with the full prefix:

- `python scripts/container.py web`: start Uvicorn on `0.0.0.0:$PORT`
- `python scripts/container.py db <command>`: run the explicit lifecycle surface documented in
  [database-contract.md](database-contract.md)
- `python scripts/container.py ready`: compatibility entry point for runtime-profile readiness

The image never embeds Extension code. Before a web process starts, Core resolves each enabled
Peer selection at its exact Registry version and restores missing Python wheels through the same
Runtime consumer used by extension management. This startup provisioning does not change a pin or
install disabled releases. The separate readiness command and `/readyz` remain read-only and never
install packages.

An explicit Extension selection may install the user's chosen Python release while the Peer is
paused. Core validates the wheel through the same-origin Simple index and protects Core-owned
Distributions, then restarts the whole process because installation can change shared dependencies.
At cold startup, Core only discovers and verifies the exact enabled packages after provisioning; it
does not perform an implicit install from `start_enabled`. Entry points are discovered through
`importlib.metadata`; there is no target overlay or `sys.path` mutation.

The canonical service contains `curl` for Heroku release logging and uses the same full default
command in Docker and Heroku. The separate release guard is deliberately harmless: Heroku config
vars are shared by every dyno in an app, so putting a migration owner URL there would also expose
it to the web process.

`Dockerfile.postgrest` wraps the digest-pinned upstream PostgREST image only to bind its
server port to Heroku's runtime `$PORT`. The upstream image has no shell, so the wrapper
copies one static BusyBox executable from a separately digest-pinned official image and
uses it only as the entry-point interpreter. JWT and database configuration remain runtime
inputs.

## Local Compose

`docker-compose.yml` defines the canonical local and SSH-transported topology:

- `postgres`: digest-pinned PostgreSQL 17 with pgvector and a health check
- `init`: a one-shot development-profile lifecycle initializer
- `core`: the same image using the unprivileged `inkcre_core` login
- `postgrest`: digest-pinned PostgREST using the unprivileged `authenticator` login

Start the stack:

```bash
docker compose up --build
```

The canonical agent-friendly entry point is `svc dev ensure database --repo . --json`.
Override direct-Compose ports with `POSTGRES_PORT`, `CORE_PORT`, and `POSTGREST_PORT`. Set
`INKCRE_COMPOSE_PROJECT_NAME` to give each worktree or agent a separate network and volume:

```bash
INKCRE_COMPOSE_PROJECT_NAME=inkcre-agent-a \
POSTGRES_PORT=55432 CORE_PORT=58000 POSTGREST_PORT=53000 \
docker compose up --build
```

Cleanup is bounded by the same project identity:

```bash
docker compose -p inkcre-agent-a down --volumes
```

Compose defaults are development-only; production credentials come from protected runtime
secrets.

## Health Semantics

- `/livez` is process-only
- `/readyz` requires a migrated database and completed runtime bootstrap
- the Compose health check deliberately uses liveness; routing platforms should use
  readiness before sending traffic

## CI Evidence

The artifact job in `.github/workflows/ci.yml` builds the source image and PostgREST, then proves
the fresh-database chain through duplicate init, JSON readiness, the
production PostgREST wrapper binding from `$PORT`, JWT read/write and denial behavior,
reset of a database containing an extra Extension followed by a second reset against the same
expected catalog and seed, Alembic metadata, and web liveness/readiness. It uses no Neon
or Heroku state.

PR CI initializes a separate neutral runtime database, exports password-free role SQL and a
PostgreSQL 17 whole-database schema dump, and restores them in an empty cluster. It checks
the restored role definitions and Alembic head/schema before initialization provisions runtime
credentials and builtins. After strict
required checks admit that tree to protected `main`, `artifact-publish.yml` independently repeats
that export in the release workflow,
embeds its same-run schema, builds the canonical service image, and publishes the image commit tag
and immutable digest. First-party wheels are independently
built and published by `extension-publish.yml` only for changed Extension directories from the
same protected-main push. Application image promotion is not coupled to Extension publication.
The production controller is admitted by the successful exact-main artifact run and rechecks that
its source is still current `main`; it does not wait for duplicate checks on the merge commit.
Production pulls that digest and transfers the same image content to Heroku; it does not rebuild
core code. Only a successful production probe moves the mutable `stable` discovery channel.

When no local Docker-compatible runtime is installed, `svc.local.json` may select the
checked-in SSH provider implemented by `scripts/dev_database_provider.py` and
`scripts/remote-compose.sh`. It executes this same Compose file, records the remote daemon
identity, allocates dynamic loopback ports, and keeps cleanup instance-bounded. CI remains
the authoritative clean-environment container proof.
