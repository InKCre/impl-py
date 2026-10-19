# Executable Database Contract

Shared Peer, capability, and wire semantics are owned by `../_shared/20-product-tdd/`. This document
owns how impl-py's database roles, lifecycle commands, readiness, and deployed profiles realize those
contracts. A cross-unit semantic change must update the Hub owner before this runtime projection.

## Authority Boundary

`impl-py` owns the executable PostgreSQL protocol artifact without becoming a privileged central backend. Native
runtimes and PostgREST callers are peer transports over the admitted `inkcre` schema。

| Principal | Shape | Authority |
| --- | --- | --- |
| `authenticated` | NOLOGIN, NOINHERIT | complete admitted `inkcre` protocol capability |
| `anonymous` | NOLOGIN, NOINHERIT | none |
| `authenticator` | LOGIN, NOINHERIT | may switch only to `authenticated` after JWT validation |
| `inkcre_core` | LOGIN, INHERIT | native runtime member of `authenticated` |

Migrations connect through `MIGRATION_DATABASE_URL`. The two login passwords are supplied
at execution time as `CORE_DATABASE_PASSWORD` and `POSTGREST_DATABASE_PASSWORD`; they never
live in a migration or image. Application and PostgREST runtimes never receive the migration
owner URL. Initialization always reconciles those two password-bearing roles, including after
restoring the password-free role definitions from a release artifact.

## Lifecycle

The OCI artifact exposes one ordered initializer and independently callable primitives：

```text
db init --profile runtime --environment preview|production
db init --profile development
db migrate
db provision-roles
db reconcile-builtins
db seed-dev
db ready --profile runtime|development --json
db reset-dev --confirm reset-development-data
db contract --json
db schema --json
```

`init` is the only command consumers need to know for a fresh database. Every command is
safe to repeat. Catalog reconciliation is import-safe and does not start FastAPI,
APScheduler, or extensions.

`reset-dev` 同时要求 exact confirmation 与 database-owned `environment=development` identity；runtime、preview、
production、missing/unknown identity 均拒绝。Development seed只含 fixed protocol state，绝不复制 production rows。

## Protocol Surface

`inkcre` 是 authenticated peers 的 versioned relation/function schema。`inkcre_internal` 保存 migrations、JWT
checks、trigger helpers 与其他 non-protocol mechanics。Internal helper 不得为了让 peer EXECUTE 而泄漏进 public
schema。

Current admitted application relations包括 blocks/relations、extensions、
`extension_peer_participations`、sources/jobs、storage catalog、
`storage_blobs` 与 embeddings。`storage_blobs` 是 PostgreSQL binary storage 的 bytes backing relation，不是
storage type、block kind 或 MIME authority。

`extensions` owns shared Extension identity, config, and producer-owned state. A row in
`extension_peer_participations` owns one Peer’s exact native distribution/version, enabled intent,
and config schema for that exact selection. The v2 migration snapshots the former deployment-wide
version, enabled-peer array, and config schema privately, then creates unresolved exact-version
participations for the old enabled peers. Their config schema remains only in the private snapshot
until a Host resolves the exact Distribution and version. The old `set_extension_peer_enabled` RPC
and global Extension version/enable/schema columns are removed. Both native `inkcre_core` and
authenticated PostgREST Peers can read and update the shared state object; anonymous callers cannot.
Participation writes use the authenticated peer role under the same JWT boundary.

## PostgreSQL Binary Storage Protocol

`peer-database-runtime-v3` retains the bytes-only operations required by equivalent peers：

- `create_storage_blob(bytea) -> uuid`：raw `application/octet-stream` request；
- `read_storage_blob(uuid) -> inkcre."application/octet-stream"`：raw bytes response；
- exact `storage_blobs` row update/delete：pointer-stable U/D。

PostgREST 14 对 raw byte response 需要 explicit response media-type domain；raw request 只依赖 single unnamed
`bytea` argument。Readiness separately proves both signatures。

Storage pointer JSON belongs to the `postgresql_binary` handler。Peer/application只持有 opaque pointer string；
bytes、MIME、filename 与 resolver semantics不复制到 pointer。Core native commands通过 GraphUnitOfWork 中的 StorageRepository 异步进行
C/R/U/D；browser peer通过 raw RPC + exact relation update/delete获得等价 capability。

Deleting a referenced `storages` catalog row is `RESTRICT`。Changing/deleting a blob does not query or rewrite blocks，
所以 block timestamps、embedding 与其他 peer cache 不自动代表 storage bytes freshness。

## Peer Discovery Protocol

`peers` 持有 deployment Peer identity、应用版本、owner config/schema、完整 capability/inbound snapshot 与
`lease_expires_at`。Peer runtime 在注册时写入自己的 `application_version`，供管理界面识别实际运行的应用；它不替代
Extension Host SDK 的兼容性版本。旧行可以暂时为 `NULL`，Peer 再次注册后自然补齐。注册只在首次创建 identity 时写入
默认名称，后续刷新应用版本和 schema 时保留 Human 修改的名称、配置与标签。Capability snapshot 是 runtime-owned
derived projection；`labels` 不参与 routing，readiness 不进入 advertisement。HTTP inbound 的 absolute URL 只由当前
Peer 的 `config.http_public_base_url` 与固定业务 path 组合，Peer row 不再持有通用 `rest_api_url`。

`renew_peer_lease(peer uuid, ttl_seconds integer) -> timestamptz` 是 `SECURITY INVOKER`、database-time helper。
调用者提供正 TTL；不存在的 Peer 或非正 TTL 明确失败。普通 Peer row update 不续租，graceful shutdown 清空 expiry，
abrupt loss 等待 lease 自然过期。

## Readiness

`db ready --json` has stable exit semantics and checks：

- artifact migration head equals database head；
- immutable environment and contract revision；
- role attributes、memberships、table/sequence/schema/function ACLs；
- exact public relation/function set and internal-surface exclusion；
- admitted RPC argument names/types、return database type、set shape、volatility and transport media type；
- checked-in core-owned catalog；Extension-contributed Source types are published by their runtime and are not readiness
  prerequisites before activation；
- fixed development seed when selected。

只检查 function names/EXECUTE 权限不足以证明 wire contract。Readiness errors remain component-level and never expose
database URLs or provider exceptions。FastAPI `/readyz` 使用同一 unprivileged check。

## JWT And PostgREST

```text
algorithm: HS256
role: authenticated
issuer: inkcre-peer
audience: inkcre-api
required: role, iss, aud, iat, exp
future iat tolerance: 60 seconds
```

PostgREST exposes only `inkcre`，connects as `authenticator`，uses denied `anonymous` fallback，and invokes
`inkcre_internal.check_jwt` before every admitted request。FastAPI peer middleware validates the same vectors。

JWT 没有强制最大寿命。`JWT_DEFAULT_LIFETIME_SECONDS` 是 Core 签发器的默认值（60 秒），
`TWITTER_X_MONKEY_JWT_LIFETIME_SECONDS` 是 Twitter x-monkey 会话初次签发及续签的默认值（604800 秒，7 天）；
二者必须为正整数，不参与 token admission。调整默认值影响后续签发，不会撤销已签发的有效 token。
普通 Web Peer 仍默认签发 24 小时 token。有效期、签名、必需 claims 和未来 iat 容差仍参与验证。

## Canonical Production Profile

[`deploy/profiles/production.json`](../../deploy/profiles/production.json) 是 checked-in non-secret discovery surface。
它发布 environment URLs、Peer IDs、contract revision、currently delivered migration head、protocol schema、
anonymous policy 与 JWT claims；不包含 credential、database URL 或 mutable image tag。

Profile 的 migration head 是 delivery truth，不自动等于当前 dirty worktree/newest local migration。RSS/Memos
implementation 或 migration verification 不授权修改 production profile 或执行 production migration。

## Versioning

`db contract --json` reports the executable contract revision plus the image source revision.
Every production candidate also contains `/app/database-contract/`:

- `database-roles.sql` recreates the password-free global principals required by policies;
- `database-schema.sql` is a PostgreSQL 17 whole-database schema dump plus only the Alembic and
  contract-state rows needed to resume the lifecycle from the neutral `runtime` identity;
- `runtime-contract.json` records the executable contract that produced the dump;
- `manifest.json` binds both SQL files and the complete runtime contract to the opaque contract
  revision and source SHA.

The dump comes from a separate fresh database initialized with the runtime profile; it contains
no application row. `db schema --json` verifies the embedded files against that manifest.
Core-py publishes SQL, not client-specific generated types; consumers restore the role and schema
files, then use their own mature language tooling.

Published core images are addressed as:

```text
ghcr.io/inkcre/impl-py:<40-character-commit>
ghcr.io/inkcre/impl-py@sha256:<digest>
```

The digest is the consumption boundary. The commit tag identifies a published candidate and
`main` discovers the newest published main candidate. `stable` moves only after the exact image
content passes Heroku production smoke; consumers resolve it once to a digest before use.
Breaking protocol changes must increase the contract revision and prove append-only migration,
readiness, generated peer projection, and portable PostgREST/native acceptance together.
