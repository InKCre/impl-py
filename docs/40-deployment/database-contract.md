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

## S3 兼容存储和 WebDAV

在部署上运行 `db reconcile-builtins` 登记 `s3` 和 `webdav` 两种可写类型。通过已认证的
PostgREST 向 `/storages?select=id` 提交实例配置，网络请求使用 `Content-Profile: inkcre`
和 `Prefer: return=representation`。升级所有需要读写该存储的 Python 或 TypeScript Peer 后再选用新实例。

```json
{
  "type": "s3",
  "nickname": "采集对象存储",
  "config": {
    "endpoint": "https://my-bucket.s3.us-west-2.amazonaws.com/inkcre/",
    "region": "us-west-2",
    "access_key_id": "<access key id>",
    "secret_access_key": "<secret access key>"
  }
}
```

```json
{
  "type": "webdav",
  "nickname": "采集文件目录",
  "config": {
    "endpoint": "https://dav.example.org/remote.php/dav/files/collector/InkCre/",
    "username": "collector",
    "password": "<app password>"
  }
}
```

- S3 的 `endpoint` 指向已建好的 bucket 或对象前缀。兼容服务也可以使用
  `https://s3.example.org/my-bucket/inkcre/` 这样的路径式地址。凭据需要对象的 GET、HEAD、PUT、
  DELETE 权限，服务需支持条件 PUT。临时凭据可加 `session_token`，到期前更新实例配置。
  AWS S3 在缺少 `s3:ListBucket` 权限时可能用 403 表示对象不可访问，更新会返回权限错误。
- WebDAV 的 `endpoint` 指向已建好的 collection，使用 HTTPS 和 Basic Auth。服务需支持
  `If-None-Match: *` 创建和 `If-Match: *` 更新。
- 两种配置都可设置 `timeout`（默认 30 秒）和 `max_bytes`（默认 64 MiB），后者同时限制读写。
  `storages.config` 对部署内已认证 Peer 可见。凭据应限定在专用 bucket、前缀或目录。
- 浏览器直连需要存储域允许 Web Peer origin 的跨域请求。允许 GET、HEAD、PUT、DELETE 以及
  `Authorization`、`Content-Type`、`If-Match`、`If-None-Match` 请求头。S3 还需允许
  `x-amz-*` 请求头，并向浏览器暴露 `ETag` 响应头。独立的存储域在 WebExtension
  中也可通过显式主机权限访问。

用创建返回的 ID 设置 Source 的 `storage`，或设置部署默认值
`inkcre-cli config replace core.source --schema-id core.source.config.v1 --input-json '{"default_storage":123}'`。
RSS 附件通过 `target_storage_id` 选择存储。WebExtension 的「PDF 可写存储 ID」也可以填写该 ID。

Block 保存相对于实例地址的 UUID 指针。轮换凭据可编辑原实例配置，更换 bucket、前缀或目录时应创建
新实例，以保留已有 Block 的读取路径。外部对象写入先于图提交。提交结果不明确或清理失败时，
对象可能留在存储中。备份和恢复应覆盖 PostgreSQL 和所用的 bucket 或目录。

## SFTP 存储

运行 `db reconcile-builtins` 登记 `sftp`。用上文同一个 `/storages?select=id` 入口创建实例，并把
返回的 ID 填入 Source 的存储配置。保存到此实例的 Block 由具备 SFTP 访问权限的 Python Peer 读取。

```json
{
  "type": "sftp",
  "nickname": "附件服务器",
  "config": {
    "host": "storage.example.org",
    "port": 22,
    "username": "inkcre",
    "directory": "/srv/inkcre/objects",
    "known_hosts": "storage.example.org ssh-ed25519 <trusted host public key>",
    "private_key": "<OpenSSH private key text>"
  }
}
```

- `directory` 是该 SFTP 账户看到的绝对路径。预先创建专用目录，并授予该账户读写及改名权限。
  更新对象要求服务器支持 OpenSSH 的 `posix-rename` 扩展。
- `known_hosts` 保存通过可信渠道核实的 OpenSSH 主机密钥条目。非默认端口使用
  `[storage.example.org]:2222` 这种主机写法。可以用 `password` 或 `private_key` 认证，
  加密私钥另填 `private_key_passphrase`。
- `timeout` 默认 30 秒，`max_bytes` 默认 64 MiB 并同时约束读写。存储目录中的 UUID 子目录供 InkCre
  管理。连接中断可能留下 `.inkcre-stage-*` 或 `.inkcre-delete-*` 目录，确认对应传输结束后可清理。
  备份需覆盖数据库和附件目录。

## IPFS 归档和 Google Drive 存储

运行 `db reconcile-builtins` 登记 `ipfs` 和 `google_drive`。通过 `/storages?select=id` 创建实例，
再把返回的 ID 填入 Source 存储配置。Python Peer 需要可以访问配置的 Kubo RPC 或 Google Drive。
两者的单次 HTTP 请求 `timeout` 默认 30 秒，`max_bytes` 默认 64 MiB。

### IPFS

```json
{
  "type": "ipfs",
  "nickname": "私有 IPFS 归档",
  "config": {
    "endpoint": "http://127.0.0.1:5001"
  }
}
```

- `endpoint` 是私有 Kubo RPC 的根地址或反向代理挂载前缀，驱动会追加 `/api/v0`。受控代理
  可选 `bearer_token`。运维应把 RPC 放在受控网络内，并备份持有内容的 Kubo 仓库。
- 每次写入创建独立且递归固定的目录根。释放其中一次写入的 pin 时，共用底层数据的其它写入仍可读。
  可用性取决于有在线节点保留数据。公开网络中的其它副本有独立的生命周期。
- 支持创建、读取和释放本次写入的 pin。内容变更需要写入新对象并更新 Block 指针。对象原地更新会返回
  不支持的错误。

### Google Drive

```json
{
  "type": "google_drive",
  "nickname": "Google Drive 附件",
  "config": {
    "folder_id": "<authorized folder ID>",
    "client_id": "<Google OAuth client ID>",
    "client_secret": "<Google OAuth client secret>",
    "refresh_token": "<offline Google OAuth refresh token>"
  }
}
```

- 使用同一个 OAuth 客户端和账号取得允许后台访问的 `drive.file` refresh token。目标文件夹需由该应用
  创建或通过 Google Picker 等方式授权给该应用。填入文件夹 ID 本身不会授予权限。
- 使用 Drive 文件 ID 读写文件。驱动核对文件的应用私有标记和目录归属，移动出配置目录会影响存储读取。
  删除已管理文件需要该账号具备永久删除权限。凭据撤销后需重新授权并更新实例配置。
- 更新保持 Drive 文件 ID。并发替换由 Drive 处理，应用层可以按稳定 ID 重新读取结果。

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
Core-py publishes SQL, not consumer-specific generated types; consumers restore the role and schema
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
