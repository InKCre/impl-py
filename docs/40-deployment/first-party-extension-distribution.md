# First-Party Extension Distribution

Core stores one shared Extension identity in `inkcre.extensions`: `name`, `nickname`, `config`,
and producer-owned `state`. Each Peer stores its exact native selection and enabled intent in
`inkcre.extension_peer_participations`, keyed by `(name, peer_id)`. A participation includes the
distribution, exact version, optional `source_repository`, enabled intent, and the config schema
published by that exact release.
Selection, enablement, and actual process runtime remain distinct states. No first-party rows are
seeded or synchronized.

Select a repository-backed release with
`inkcre-cli extension select COORDINATE --distribution python --version VERSION --source-repository https://github.com/OWNER/REPO`.
Core reads the exact GitHub Release, chooses a compatible wheel, and verifies its SHA-256 from the
Release attachment. The wheel's native installed record supplies its project and entry point.
The shared tag and release description are defined in
[Extension Release and Installation](../_shared/20-product-tdd/extension-release-and-installation.md).

On enable or cold restore, Core can reuse a wheel after checking the repository recorded by pip
in its installed archive metadata. If the metadata does not establish the repository, Core
reads the exact GitHub Release and checks the installed archive SHA-256 against its attachment.
A conflicting wheel prevents the selection from running.

A participation with a null source uses the configured Registry and its same-origin
`/simple/<normalized-project>/` association. This path checks the exact Registry Release during
enable and cold restore. An already-selected yanked Release retains its existing warning and
admission behavior. Omit `source_repository` when selecting a version to keep the current source,
or send null to select from the configured Registry.

When the exact Distribution is absent, Core first downloads and validates its wheel without changing
the interpreter. It then closes Peer execution, drains jobs and scheduled callbacks, closes running
extensions, and applies the wheel with ordinary `sys.executable -m pip install` to the shared Core
environment. Pip may resolve dependencies from its configured package index and may change packages
used by other Extensions. After apply, Core replaces the process with a fresh one so Python imports
cannot continue using modules loaded before the environment change. An apply failure is reported and
leaves the Peer paused with the installation error. An installed wheel that matches the selection
skips this lifecycle.
The standard `inkcre.core.extensions` entry point is discovered globally and every loaded Extension
module is verified against the installed wheel file record. There is no per-Extension target
directory, custom ZIP loader or module search-path rewrite. The production image excludes the
first-party producer source trees so the installed wheel owns its modules.

Peers may select different versions of the same shared Extension. Changing one Peer’s exact
selection is rejected while that Peer has the Extension enabled; other Peers’ selections do not
block it. A changed pin clears its config schema until the selected release starts and provides a
new schema. A process that already imported another version must restart; Core does not hot-load the
new class. `running` and `loaded_version` report observed runtime facts and are never inferred from
the durable enabled intent.

V2 management commands select or remove a Peer’s exact pin, enable or disable that Peer’s intent,
and report actual runtime status. A disabled pin may be removed; an enabled participation must first
be disabled. A legacy enabled member migrates to its exact prior version with an unresolved
distribution. Cold restore resolves only that exact Registry version and never substitutes the
latest release. Failed resolution preserves the old intent and reports the error.

The first-party Extensions are independent PEP 420 wheels with the standard entry-point group
and producer metadata in their `pyproject.toml`. That project version determines the Python
Distribution's Release version. Feature pull requests carry project-local Towncrier fragments and leave versions and
changelogs unchanged. After checked `main`, one generated Release PR consumes pending fragments,
applies each project's independent SemVer bump, and renders its changelog without publishing.
Repository orchestration owns project impact and next-version calculation；Towncrier owns fragment
validation and rendering。

The initial feature merge also wakes the publisher, but no project version has changed yet, so its
selection is a no-op. The Release PR merge runs repository checks against the generated release
state; only that checked `main` revision gives the existing publisher a new version to select and
publish. Leaving the Release PR open defers publication while later fragments update the same plan。

Repository CI discovers the producer set and rejects missing or invalid project intent。Publication
never runs from the Release PR。

The trusted pull-request preview controller reuses that same discovered producer set. Its dedicated
PDM `extension-preview` tooling group builds and verifies all discovered wheels, then the released
`inkcre-extension-toolkit` projects one explicit Python inventory into a static sibling Registry.
The exact-head workflow run deploys that tree directly to the dedicated Cloudflare Pages project;
required checks neither upload nor hand off wheels or deployable Registry output. The facade exposes
only exact Release reads, PEP 503 Simple HTML, wheels, and PEP 658 metadata. It adds no Registry
runtime, mutable lifecycle, token, or Core-hosted static route.

Preview delivery treats a successful Pages deployment command as sufficient delivery evidence. It
passes the deterministic PR alias to Core without downloading every published file, comparing
bytes, bypassing caches, or waiting for edge convergence. Consumer acceptance remains a separate
manual or black-box activity and does not block the Heroku preview from starting.

[Python wheel 正式发布](../../.github/workflows/extension-publish.yml)在受保护的 `main` 上发现
producer。自动运行比较该次 push 的前后版本，只选择新项目和升版项目。它使用冻结的工具链生成 wheel，
再通过 Toolkit 的 `python wheel finalize` 写入原生 `.dist-info/inkcre-extension.json` 并校验 wheel。
预览也使用同一个 finalize 步骤。

正式发布前，capture 再次检查提交仍属于 `main`、该插件的构建输入未变，然后保存 wheel、源提交、
元数据和 SHA-256。publish 在 `production` 环境恢复并校验原候选，用其标签创建或恢复 GitHub Release
草稿。只有附件摘要与保存的 wheel 一致时才公开，随后从 GitHub 读回标签、正文和附件摘要。
跨仓格式见[扩展发行与安装](../_shared/20-product-tdd/extension-release-and-installation.md)。

切换时，维护者可用 `workflow_dispatch` 的 `INITIAL_ONLY` 为一个 producer 补上首个仓库 Release。
后续更新按普通升版流程发布。失败时使用原 run 的 **Re-run failed jobs** 恢复保存的 wheel，候选保留 90 天。
仓库 Release 公开后的修订需要项目升版。

新发行的用户文档放在 producer 的 `docs/user`，随该 Release 标签对应的源码浏览。
已有 Registry 发行可以沿原地址恢复。历史发行的用户文档也继续由其已有记录提供；
[手动文档工作流](../../.github/workflows/extension-documentation-publish.yml)仅供维护历史 Registry 发行。

Official production publication and deployment jobs require
`github.repository == 'InKCre/impl-py'`. A fork may build and test normally, but it cannot enter the
official production environment merely because its `main` branch published an artifact. Fork
owners deploy through the explicit Render or Heroku self-host workflow instead.


## Async Host compatibility window

Core Host 0.2 uses Runtime SDK async startup and persistence capabilities. Host operations and
Extension config/state mutations must be awaited; in-memory config projection and typed mutation
callbacks remain synchronous. ExtensionStateService owns each short business transaction;
app/persistence/extension owns SQL and session creation. Row locks serialize state transforms. A
participation row owns only one Peer’s exact selection, enabled intent, and version-scoped config
schema; shared Extension config and state remain deployment-wide.

Selection and Registry-origin database reads finish before release HTTP and wheel acquisition.
Blocking artifact clients run in a worker without a database session. Startup failure or cancellation
withdraws the current publication and releases its runtime claim.

Core Host SDK 0.3 adds `app.http.get_public_http_base_url()` without changing the async lifecycle
or persistence API. Its authority is `app/version.py`, independently of the Core service package
version in `pyproject.toml`; preparing a service Release PR does not change the Host SDK version.

Core Host SDK 0.3.1 adds batched JSON identity lookup and transaction advisory locking to the
existing Graph repository. Twitter archive reconciliation requires `>=0.3.1 <0.4.0`.

Host SDK 0.3.3 adds optional PostgreSQL blob row locks to shared graph namespace locks, Block/Relation row locks and repeatable-read
graph scopes. Existing consumers retain their defaults. The CalDAV Distribution requires
`>=0.3.3 <0.4.0` because its conditional writes depend on these capabilities.

GitHub, Learn English, Mail, RSS, and Telegram retain their SDK 0.2 lower bound and support
SDK 0.3 through `>=0.2.0 <0.4.0`. Memos uses the new HTTP address API and requires
`>=0.3.0 <0.4.0`. Compatibility metadata changes require new immutable Extension
releases; an old wheel's range must not be widened. Before upgrading a deployment, disable affected
old Extensions, adopt the matching Core/Extension releases, restart where a loaded wheel was replaced,
and then enable them. A migrated exact-version legacy intent is not silently removed on failed cold restore.
Python and Module Federation Releases are versioned independently for the same Extension identity.
Publish a new version only for the Distribution whose artifact or compatibility contract changed;
documentation is also attached to the exact native Distribution Release. A Peer selects the
Distribution and version it can execute, so availability of another Distribution does not imply
that the selected one exists.
