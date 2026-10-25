# Heroku

## Artifact And Authority Model

Heroku consumes the same canonical provider-neutral OCI image published to GHCR:

- the exact GHCR digest is tagged and pushed as the Heroku `web` image without rebuilding core;
- `heroku-release` is a lightweight no-op guard containing no core code;
- `Dockerfile.postgrest` adds only a `$PORT` adapter, using a digest-pinned static BusyBox
  interpreter because the digest-pinned PostgREST runtime contains no shell.

The protected GitHub delivery job runs database lifecycle commands before releasing images.
This is intentional: Heroku config vars are available to every dyno in an app, so putting a
migration-owner URL into Release Phase would also expose it to the web process.

Core Heroku config contains only an `inkcre_core` URL. PostgREST config contains only an
`authenticator` URL. The direct Neon owner URL exists only in the protected delivery job.
Heroku supplies compute, routing, and runtime configuration; it owns neither the build nor
the database.

正式生产、预览及复用本交付命令的自托管部署，将应用日志后端配置为 `postgresql`。
Core 通过自身 PostgreSQL handler 写入符合原有采集条件的应用日志，同时保留 console
输出；这与 Heroku 采集 console 无关。可选 OTLP 出口仍需 Peer 显式开启。

The retired buildpack surfaces (`Procfile`, `requirements.txt`, and `app.json`) are no longer
checked in. PDM owns dependency resolution and the OCI Dockerfiles own every deployed process,
so leaving Heroku does not require reconstructing runtime behavior from provider-specific
files.

## Pull Request Previews

Trusted same-repository pull requests currently use:

- app names `inkcre-core-py-pr-<number>` and `inkcre-postgrest-pr-<number>`;
- a sibling static Registry alias under
  `https://pr-<number>.inkcre-core-py-extension-registry-preview.pages.dev`;
- pipeline `inkcre-core`, stage `review`;
- container stack in the US region;
- one Eco `web` dyno for each peer transport;
- matching seven-day Neon branch `preview/core-py/pr-<number>`;
- no Heroku addon.

应用 Preview 在读取完整 PR 文件清单后，仅对全部变更都位于根目录 `tests/` 的候选跳过 wheel、
镜像、Registry 和 Heroku 交付。重命名同时检查新旧路径；文件清单不完整、读取失败或查询前后
head／base 变化时，继续原交付流程。不将此规则扩展到 `scripts/tests/`，因为镜像包含整个
`scripts/`。判定来自受信 controller，不执行候选代码。

Neon 分支仍由原必需检查创建或复用；它的成功状态继续代表实际供给，不表示一次无资源的跳过。
打开的 PR 已有现场不会因后续只剩测试改动而删除；关闭时的 Registry、Heroku 和 Neon 清理
独立于路径判定，始终按原身份边界执行。此策略减少应用交付，不承诺纯测试 PR 零资源成本。

The branch workflow owns only exact isolated branch creation/deletion. After the repository,
portable-runtime, and branch checks pass for the exact PR SHA, preview delivery:

1. installs the frozen `extension-preview` PDM group, builds all discovered first-party wheels,
   and asks `inkcre-ext preview build` for one Python-only static Registry facade;
2. builds images before receiving deployment secrets;
3. reverifies the exact PR head and deploys the facade directly to the dedicated Pages project;
4. verifies the exact database branch and TTL;
5. removes inherited provider-created protocol roles only on first bootstrap;
6. runs the PR artifact's complete preview-profile initialization and readiness;
7. derives an `inkcre_core` URL without logging it;
8. configures `EXTENSION_REGISTRY_URL` to the deterministic sibling alias before releasing or scaling
   Core, then configures and releases both apps against the exact database branch;
9. forces both formations to `web=1:eco`, probes Core `/livez` plus `/readyz`, and
   verifies authenticated PostgREST read/write plus anonymous and wrong-secret denial.

Required preview configuration is the exact non-secret project variable
`CLOUDFLARE_EXTENSION_PREVIEW_PROJECT=inkcre-core-py-extension-registry-preview`, plus the
preview-environment `CLOUDFLARE_ACCOUNT_ID` and minimally scoped `CLOUDFLARE_API_TOKEN` secrets.
Fork pull requests never enter this environment. PR-close delivery replaces the exact Pages
branch alias with a trusted no-cache tombstone before deleting the two deterministic Heroku apps;
the independent Neon workflow remains the database-branch cleanup authority.

The repository-level `JWT_SECRET` is the single signing-key authority for canonical
production and same-repository previews. Delivery passes that value directly to both Preview
apps on every run, so Core and PostgREST remain aligned across redeployments or app
recreation. The value stays in GitHub Secrets and never enters source, artifacts, logs, or
workflow summaries.

The owner URL never enters preview Heroku config. The Core app receives only the `inkcre_core`
URL and the deterministic sibling Registry alias; the PostgREST app receives only the
`authenticator` URL. The Pages deployment command is sufficient delivery evidence. Public edge
reads and consumer acceptance are deliberately independent and do not block Core startup.

Repository-qualified app and branch identities keep an impl-py PR and an impl-ts PR with
the same number from addressing the same review resources.

交付摘要在失败时也报告目标 SHA、Registry snapshot、数据库初始化、进程 release、Peer 广告和探测的
各自结果。数据库输出保留初始化前后的实际 migration head 与候选期望 head／contract；没有取得的观察
明确留空，不推断旧服务已经停止。Core 镜像写入目标 SHA label。Heroku 当前 release 身份另外只读查询，
该身份本身不证明本轮源码已生效。进程 Registry fallback 也不能覆盖 Peer／deployment 的优先配置，
跨仓业务验收仍需核对实际 Distribution 和 Registry 来源。

同一 PR 的 Neon 分支保留状态。若候选移除了该分支已经执行的未发布迁移，重复交付不会自动 reset、
downgrade 或补回迁移。先保留原分支作诊断：需要验证保留状态升级时，修复候选，使它能从当前 head
前进；需要验证全新候选时，将同一明确源码放到新的 PR，使现有分支 owner 从 `preview-base` 创建新的
隔离分支。原 PR 的关闭清理只在不再需要其数据与证据时执行。这里不增加隐式“自愈”或复用其他 PR
数据库的行为；失败时 Registry 可能已经更新，不表示整个组合已经就绪。

## Canonical Production

Production is one logical environment containing two peer transports:

| App | Responsibility | Database principal | Formation |
|---|---|---|---|
| `inkcre-core-production` | native core peer | `inkcre_core` | one Eco web dyno |
| `inkcre-postgrest-production` | browser HTTP peer transport | `authenticator` | one Eco web dyno |

Both apps use the container stack and address the configured canonical Neon production branch.
Peers discover their non-secret connection contract through
[`deploy/profiles/production.json`](../../deploy/profiles/production.json); credentials never
enter that profile.

`.github/workflows/production-deploy.yml` runs only after `artifact-publish.yml` succeeds for
the exact current `main` SHA. It pulls the SHA-addressed GHCR service image, verifies its
embedded schema evidence, and builds only the no-op release guard and separate PostgREST
adapter before receiving deployment inputs. Before database convergence, this lane requires the
existing production database to already satisfy the candidate contract. A schema change therefore
fails closed here and must use the separate, protected maintenance workflow below.

`.github/workflows/production-readonly-preflight.yml` is a separate protected-main manual entry
for reviewing the configured production targets. It reuses the existing strict Heroku app
resolver and Neon connection resolver, verifies the exact Neon branch with a read-only API call,
and reads the matching immutable Core image. Its database inventory runs in a read-only,
repeatable-read transaction and records the migration/contract revision, Peer leases, exact
Extension selections (including unresolved legacy distribution), pending/running Jobs, relevant
database roles and table privileges, and the visible database-session snapshot. It also compares
the Heroku Core and PostgREST database hosts, database names, roles, and JWT configuration without
printing their values. The configured Core Peer ID must equal the official deployment resolver's
deterministic ID for that Heroku app. The report includes the checked-in non-secret production
profile separately so stale declarations are visible rather than treated as live state.

This report is an observation for maintenance planning. Active Peer leases, Jobs, and database
sessions describe only the snapshot taken during the job; they do not prove that every external
Peer or direct database writer has stopped. The report never runs migrations, changes app config,
changes formation, releases an image, or starts application code.

Normal production delivery resolves role-specific URLs from the configured Neon branch ID,
confirms that `db ready` already passes for the exact candidate image, configures and releases
Core plus PostgREST, converges the Peer advertisement, and probes both public runtimes. Database
convergence is reserved for the explicit maintenance workflow. Images and config releases are
polled to a terminal state; registry login and transfer alone receive bounded retries.

The production delivery records the GHCR digest, the local image ID matched against each image pulled
back from the exact Heroku registry tag, and the Heroku release identities. Registry manifest
digests may differ after transfer, so the transfer check compares Docker's content-addressed image
configuration IDs rather than requiring textual equality between registry digest strings.

The acceptance probe requires:

- core liveness and full database readiness;
- PostgREST authenticated read and write;
- cleanup of the fixed probe row;
- wrong-secret HTTP 401;
- anonymous HTTP 401;
- both formations remaining Eco.

A failed ordinary production probe reports both application logs and leaves the observed deployment
state intact for inspection. During an explicit maintenance run, a failed probe also attempts to
scale both apps back to zero and verifies that no dynos remain; if that containment fails, inspect
the actual formations. The workflow does not pretend that rolling back only the web image can
reverse a database migration, and it never runs an Alembic downgrade.

The mutable GHCR `stable` tag advances only after this probe succeeds. Publication without
production admission leaves `stable` unchanged。

The production workflow ends with a **Core 生产发布结果** summary whenever source checkout
succeeded, including failed or cancelled delivery attempts. It records the source-declared Core
version, SHA, candidate digest, deployment/probe outcome and stable-promotion outcome.
A successful job that selected no release explicitly says it performed no deployment; its source
version is not evidence of the currently deployed version. A failed delivery may already have
changed production, and the summary does not imply rollback. The existing detailed probe summary
retains Heroku release identities and transport checks. If checkout itself failed, inspect that
step; no repository-owned summary command could run.

`.github/workflows/production-maintenance.yml` is the only production path that applies a new
database contract. It runs from current `main` in the protected `production` environment and
requires a fresh dispatch plus explicit acknowledgement that every non-Core/PostgREST Peer and
direct database writer has stopped. It first records a read-only inventory, then transfers the
exact candidate Core, release, and PostgREST images to the Heroku registry before the maintenance
window. It stops both configured Heroku apps and verifies that no dynos remain, then takes another
inventory and blocks on observed active Peer leases, running Jobs, or database sessions except
idle client backends without open transactions. This narrow exception handles pooled connections
that remain idle after both apps stop; it does not depend on an application-name allowlist. Active
sessions, idle-in-transaction sessions (including aborted transactions), non-null transaction
starts, and unknown backend or session states still block. The inventory retains every session.
Those observations do not prove that unknown or
external writers are absent; the acknowledgement remains an operational responsibility. After the
stopped-service inventory passes, the job reads the production branch's flushed WAL LSN through
the exact candidate image and creates a named Neon child branch at that LSN. The request omits
compute endpoints and expiration; the workflow verifies the exact parent, LSN, non-default status,
and that no endpoint was created. It checks current Neon branch quota before the write and reserves
capacity for both the backup branch and a preserved failed-state branch. It then runs `db init`,
readiness, app release from the already transferred and registry-verified images, and runtime probes
from the immutable candidate image. The workflow does not delete the backup branch; cleanup remains
an explicit operator action after the recovery window closes. Stable promotion follows only a
successful probe. If promotion fails after a successful probe, the healthy apps remain online while
the stable channel remains unchanged.

Maintenance runs refuse GitHub Actions reruns because a failed network response can leave the
actual migration state uncertain. A failure before the first scale operation leaves the existing
service running. A failure during shutdown or migration can leave one or both apps stopped, so
inspect both formations and the database. A failed runtime probe attempts to stop both apps again;
verify the actual formations because that containment can itself fail. If the workflow logs confirm
that `db init` did not start, verify that the schema still matches the recorded pre-maintenance
inventory before manually restoring the
previous formation. Once `db init` starts, the database may be partially migrated: keep both apps
stopped, inspect the live schema, then repair forward or restore the confirmed Neon backup branch.
Production has child branches, so a restore must use `preserve_under_name`; verify the default
branch and endpoint mappings before restarting services. Neon data restore does not roll back role
passwords, so validate current configured credentials separately. Never start the old image against
a database that may have migrated. Do not rerun the same execution; use a new approved maintenance
run only after the observed state and recovery point are understood.

## PostgREST Runtime Contract

PostgREST is a separate app, not a second process supervised inside the core dyno. It uses:

```text
PGRST_DB_SCHEMAS=inkcre
PGRST_DB_ANON_ROLE=anonymous
PGRST_DB_PRE_REQUEST=inkcre_internal.check_jwt
PGRST_JWT_AUD=inkcre-api
```

`PGRST_DB_URI` and `PGRST_JWT_SECRET` are runtime secrets. The pre-request function enforces
issuer, role, numeric dates, expiry, and maximum lifetime in addition to PostgREST signature
and audience validation.

## Secret Boundary

The GitHub `preview` environment owns its Heroku and Cloudflare authorization, LLM inputs, and
the two database-role passwords. `JWT_SECRET` and `NEON_API_KEY` remain repository secrets,
while `NEON_PROJECT_ID` and `CLOUDFLARE_EXTENSION_PREVIEW_PROJECT` remain repository variables.

The GitHub `production` environment independently owns:

- dedicated Heroku authorization;
- LLM inputs;
- `CORE_DATABASE_PASSWORD`;
- `POSTGREST_DATABASE_PASSWORD`.

It records the configured non-secret app and Neon branch identities。Production admits deployments from `main` only and does
not reuse preview authorization or database passwords. Preview and production deliberately
share only the repository signing-key authority.

The preview Heroku authorization named `GitHub InKCre/impl-py preview CD` was created with a
365-day lifetime on 2026-07-23. Rotate it no later than 2027-07-23 and revoke the superseded
authorization after a green preview deployment.

## Migration Constraint

The protected job is the single migration writer and runs
`db init --profile runtime --environment preview|production`. Rolling a web image back does
not reverse database state. Every revision must pass the fresh digest-pinned
pgvector/PostgREST runtime check and the matching Neon preview before production delivery.

## Retired Runtime

There is no persistent staging environment. The `inkcre-core` pipeline contains exactly the
two canonical production apps above; review apps exist only for an eligible pull request and
are removed when it closes.

The pipeline is intentionally not connected to a GitHub repository in Heroku. GitHub Actions
is the only review-environment controller and couples each deterministic app through the
Heroku Platform API. Reconnecting Heroku's legacy GitHub integration would create an
all-events repository webhook and a competing review-app authority.

The former `inkcre-core-staging` and `inkcre-pgrst` apps were deleted on 2026-07-24. Deleting
the staging app also removed its Logtail addon and Better Stack drain. Neither app had a
custom domain. GitHub no longer contains their staging environment or the all-events Heroku
webhook.
