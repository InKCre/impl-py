# Development Environment

## Local Requirements

- Python 3.12
- PDM 2.28.0
- local Docker, or one Docker daemon reachable through an SSH-config alias
- a populated `.env` file

The checked-in Python selector is `.python-version`. The foundation CI uses the same
Python selector and PDM version.

## Local Startup

The supported complete runtime is an SVC worktree capability:

```bash
svc status . --json
svc dev ensure database --repo . --json
```

It starts PostgreSQL/pgvector, performs deterministic development initialization, then
starts both core-py and PostgREST. Committed `svc.json` selects local Docker. An ignored
`svc.local.json` may select `ssh`, one SSH-config alias, and the remote Docker executable;
tracked files never own a hostname, user, key, or machine path.

The SSH provider sends an allowlisted build context and one bounded Compose payload to the
remote host. Remote services publish dynamic remote-loopback ports and an instance-owned
OpenSSH control tunnel maps independent local-loopback ports to them.
The descriptor keeps the absolute control-socket path, while SSH runs from its parent directory
with the short socket name so long worktree paths do not exceed Unix-domain socket limits.

Runtime state is written to `.runtime/database/<core-svc-instance>/`. Its `runtime.json`,
`profile.json`, and `readiness.json` record:

- owner repository and core-py SVC instance;
- exact Compose project and Docker daemon ID;
- source revision and dirty-source fingerprint;
- database contract revision and migration head;
- core and PostgREST loopback URLs.

Peers must consume that exact profile/descriptor. Sharing only an SSH alias, daemon, port,
or contract version does not prove that core-py and client-web use the same database.
Only core-py owns Compose startup, reset, volume deletion, credentials, and tunnel cleanup.

After core and PostgREST are reachable, the owner projects the dynamic core loopback URL into the core Peer's
database-owned `config.http_public_base_url`. It then waits for that exact Peer to publish the three built-in HTTP
capabilities and a live database-time lease before writing a converged descriptor. Attached client-web runtimes only verify
that snapshot; they do not rewrite the owner Peer, reset the database, or stop the owner runtime.

## Stopping the development runtime

When the owning task no longer needs its disposable database, run
`svc dev stop database --repo <owning-worktree> --json` before removing the worktree.
This invokes the existing runtime owner with that worktree's exact instance: it removes
its Compose containers, network and database volume, closes its SSH tunnel, and deletes
its runtime descriptors and credentials. Development data in that volume is deleted.
The provider comes from the recorded runtime, so stopping an SSH runtime does not require
a local Docker daemon or copying the provision environment into a second declaration.
Do not use stop to repair a readiness mismatch or against another task's runtime.

## Shared Runtime Boundary Diagnostics

An attached peer proves the selected database runtime by matching the owner descriptor and
readiness evidence for:

- runtime instance and owner repository;
- Compose project and Docker daemon ID;
- source fingerprint, database contract revision, and migration head;
- core-py and PostgREST loopback endpoints.

This is a contract-compatible attachment, not proof that the peer's pinned core-py image and
the running owner image are byte-for-byte identical. A different source revision or image is
valid while the contract revision and migration head remain compatible. When diagnosing
implementation-specific behavior, compare `source_revision` and `core_image` in
`runtime.json` with the peer's pinned image digest before assuming artifact equality.

The Compose network and PostgreSQL endpoint are currently derived from the exact Compose
project, source fingerprint, checked-in service wiring, and recorded local/remote ports; they
are not published as independent profile identity fields. If a future issue suggests that two
peers reached different databases, compare `runtime.json`, `profile.json`, and
`readiness.json` from the owner before inspecting provider state. Do not accept matching
daemon IDs, ports, or contract revisions alone. If owner, project, fingerprint, endpoints,
or readiness do not converge, stop reuse and let core-py re-establish the runtime.

External peers must not reset or tear down a core-py-owned runtime. The command path rejects
both operations; current dedicated external-attachment regression coverage proves teardown
rejection but does not separately exercise reset rejection. Treat that missing negative case,
and first-class network/database endpoint identity, as the first places to strengthen if a
shared-runtime boundary failure is observed.

For Python-only iteration against an already initialized database:

```bash
cp .env.example .env
pdm install -G dev --frozen-lockfile
pdm run doctor
pdm run check
pdm run dev
```

`doctor` and `check` do not start the application or connect to a database. The full
repository contract checks tool versions, lock/export consistency, migration integrity,
formatting, lint, settings isolation, and the complete unit-test suite.

格式与 lint 的范围由 `ruff.toml` 拥有，覆盖正式源码、测试与脚本。`tasks/` 是临时实验材料，`.agents/` 是独立维护的技能；二者不参与产品门禁。成为正式验证步骤的实验应迁入 `scripts/` 或 `tests/`。已发布迁移保持不可变；独立 CLI 使用 `pdm run --project cli check` 及自己的 CI。pre-commit 的格式、lint、类型、锁文件和迁移钩子均复用 `pdm run` 中的对应检查，锁定依赖已安装后，普通 `git commit` 不要求先激活虚拟环境。检查参数由 `pyproject.toml` 中的命令拥有；CI 的完整入口仍为 `pdm run check`。

Use narrower read-only checks while iterating:

```bash
pdm run check:foundation
pdm run format:check
pdm run lint
pdm run test
svc dev status database --repo . --json
pdm run dev:database ready
```

The test harness disables dotenv before application imports. Repository checks do not read a
developer `.env` or use its database and API credentials.

## Required Environment Variables

The checked-in baseline is `.env.example`.

Required for direct Python execution:

- `DATABASE_URL`
- `JWT_SECRET`

Lifecycle execution additionally requires:

- `MIGRATION_DATABASE_URL`
- `CORE_DATABASE_PASSWORD`
- `POSTGREST_DATABASE_PASSWORD`

Commonly needed:

- `PEER_ID`
- `PEER_NAME`
- `PEER_LEASE_TTL_SECONDS`
- `PEER_LEASE_RENEW_INTERVAL_SECONDS`
- `PEER_HTTP_TIMEOUT_SECONDS`
- `SEMANTIC_RETRIEVAL_MAINTENANCE_INTERVAL_SECONDS`
- `SEMANTIC_RETRIEVAL_MAINTENANCE_MAX_EMBEDDINGS`
- `SEMANTIC_RETRIEVAL_MAINTENANCE_BATCH_SIZE`
- `SEMANTIC_RETRIEVAL_MAINTENANCE_SCAN_PAGE_SIZE`
- `OBSRV__*`

AI Provider credentials and base URLs are shared deployment facts stored in `ai_providers.config`; they are not runtime
environment variables. The OpenAI-compatible adapter validates that database config at use time.

Credentialed semantic-quality acceptance is explicit and separate from the ordinary repository check. It requires
`INKCRE_ACCEPTANCE_AI_API_KEY`, optional `INKCRE_ACCEPTANCE_AI_BASE_URL`, exact embedding/chat model IDs and an explicitly
selected migrated `INKCRE_TEST_DATABASE_URL`; see the local Semantic Retrieval Unit TDD for the command and pass/fail
authority.

`pdm run test:database` 是无需真实 AI／IMAP 服务的 PostgreSQL 业务回归入口，具体场景清单由
`scripts/automation/repository_check.sh` 拥有。它要求显式 `INKCRE_TEST_DATABASE_URL`；未设置时直接失败，
不会将整组 skip 当作通过。数据库必须已经迁移且可丢弃：场景包含清空数据、事务回滚和迁移往返验证，
不能连接共享开发库、Preview 或生产。Memos 迁移场景还要求该独占 PostgreSQL 的 `MIGRATION_DATABASE_URL`
具有创建数据库权限；它建立随机命名的历史数据库，只往返附件所属 revision，并在结束后删除该数据库。
这保留实际 migration 编排和应用下载读回，不会回退当前业务测试库或声称验证了整条历史 downgrade 链。
CI 在自身临时 PostgreSQL service 上先初始化再执行这一组；外部服务和真实凭据验收继续由各自专项入口负责。

扩展持久化的定向入口是 `pdm run test:extensions:database`，由上述完整数据库入口调用同一份场景清单：Memos 附件图、RSS 条件采集、Mail 并发 checkpoint、Twitter archive HTTP/图与 full traversal，以及 CalDAV 原生图/事务/同步恢复。它们使用真实 PostgreSQL 和受控提供方输入，不需要 IMAP、Twitter 或 CalDAV 外部账号。可附加 pytest 参数，例如 `pdm run test:extensions:database -k full_bookmarks`。改变这些合同后应使用这个入口；只运行默认 `pdm run check`，其数据库 skip 不代表这些行为已验证。

该清单不自动接纳所有历史测试。需要真实账号、指定语料库或独立服务的 Mail vertical/newsletter、live RSS/Atom 和语义质量验收仍使用所属领域的专项入口。增加或移除持续回归时修改 `scripts/automation/repository_check.sh` 的唯一选择入口，说明其公开行为与所需资源；本地和 CI 不再各维护一份扩展路径清单。

## Database Branch Workflow In CI

The repository currently uses Neon branch automation for pull requests.

- `branching-database.yml` creates or reuses `preview/core-py/pr-<number>` for trusted PRs
- every preview branch is a seven-day child of the sanitized `preview-base`
- the branch workflow only establishes the exact isolated branch identity
- the exact PR artifact owns initialization and readiness during application delivery
- PR close deletes only the matching deterministic Neon branch

## Migration Commands

- `pdm run db:generate "message"` creates a candidate revision for review.
- `pdm run db:record` appends reviewed new revisions to the integrity manifest.
- `pdm run db:migrate` applies checked-in revisions.
- `pdm run db:init --profile development` creates the complete deterministic baseline.
- `pdm run db:ready --profile development --json` verifies it without mutation.
- `pdm run db:reset-dev --confirm reset-development-data` restores the same guarded
  baseline and refuses every non-development database.
- `pdm run check:migrations` checks the local graph, metadata registration, and release
  contract without connecting to a database.
- CI validates `migrations/revision-integrity.json` on pull requests and managed-branch
  pushes. A base branch without the manifest is a one-time hard-cut bootstrap; after that,
  protected entries and revision contents may not be modified, deleted, or renamed.

Generation and application are deliberately separate operations.

## Bounded Runtime Cleanup

```bash
pdm run dev:database reset --yes
pdm run dev:database stop
```

Both commands resolve the current core-py SVC worktree identity. Reset remains protected by
the database-owned development marker. Stop removes only the descriptor's exact Compose
project, volume, credentials, and SSH control tunnel.


## 按需数据库性能测量

在已经迁移、可写的隔离 PostgreSQL 环境中，沿用应用的 `DATABASE_URL`、`JWT_SECRET` 和 dotenv
加载方式运行 `pdm run python scripts/benchmark_database.py --nodes 100 --iterations 30 --concurrency 4`。
可用 `--output result.json` 保存结果。不要把 `.env` 当 shell 脚本 source。

脚本调用真实 `submit_graph` 与批量 Block 读取用例，每个 graph 含 N 个 Blocks、N−1 条 Relations。
一次预热后记录提交和读取的 p50/p95、成功 graph/s、错误率，以及代码 revision、Python、驱动和
PostgreSQL 版本。它测量应用数据库用例，不包含 HTTP、Resolver 或外部服务延迟；并发参数不会让
多个 task 共享一个 session。每次运行使用独立 UUID 标识数据，finally 精确清理该次 Blocks 及级联
Relations，包括提交成功但响应丢失的写入。进程被强制终止时 finally 无法保证执行，因此使用隔离环境。

该工具只按需运行，不加入 CI 性能阈值。比较结果时保持数据库资源、网络路径、数据规模和连接池配置
相同；一次当前版本测量不能证明相对同步版本的提升，也不据此自动扩大连接池。
