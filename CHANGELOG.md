# Changelog

Core release notes start from the current `0.1.1` baseline. Earlier repository history remains available in Git.

<!-- towncrier release notes start -->

## 0.9.0 - 2026-10-27

### Added

- Add a Python distribution that independently reads Capture graphs and remove the distribution-format allowlist from the shared participation protocol. Mac capture writes use standard graphs and Storage without requiring a Python Peer or Capture-specific tables. (#capture)
- Add readable names to MCP recall candidates while preserving entity references and mode-specific evidence. Candidates remain available when a name cannot be generated; read tools and the Resource protocol are unchanged. (#mcp-recall-labels)

### Fixed

- Keep runtime image builds compatible with PDM 2.28 by pinning its unearth installer dependency to 0.18.3. (#pdm-build)


## 0.8.0 - 2026-10-21

### Added

- 图事务支持共享命名空间锁、Block／Relation／PostgreSQL blob 行锁和一致性读取，保护 DAV 与普通 Peer 之间的条件写入。 (#caldav)
- 新增可配置的 Peer JWT 签发默认期限并移除 admission 的强制最大寿命；Host SDK 0.3.1 支持扩展在既有 Graph 事务中批量定位原生身份并串行合并。

### Changed

- Rename GitHub repository references from `InKCre/core-py` to `InKCre/impl-py`, including CI workflow repository checks, deployment docs, and the `cli` package Repository URL. The `core-py` peer instance name, GHCR image name (derived from repository), and `host-sdk = "core-py"` SDK identifier are preserved. (#rename-repository-impl-py)


## 0.7.2 - 2026-10-14

### Changed

- Extensions can now use independent, exact distribution selections and enabled intent per Peer while sharing extension identity, configuration, and state. (#129)

### Fixed

- Canonical production delivery now stops when either configured Heroku app cannot be read or does not match its expected identity, instead of treating lookup failures as permission to create an app. (#132)
- Before a new Core web process starts, it restores only this Peer’s enabled exact Python pins through Registry validation and fails startup if restoration would change existing Core, Runtime SDK, or base package versions. (#133)
- Cold startup resolves an enabled legacy extension only after verifying its exact Python release, preserving the pin and enabled intent while recording the distribution. (#135)
- Core production schema changes now use an explicitly confirmed maintenance workflow that stops configured services, transfers exact process images before downtime, checks observed database activity, and confirms a branch snapshot before migration. (#137)


## 0.7.1 - 2026-10-03

### Fixed

- 修复 Heroku 交付将应用日志后端强制设为 none 的覆盖：生产、预览及复用交付命令的自托管部署恢复 PostgreSQL 写库，console 保留，OTLP 仍默认关闭。 (#observability-pg-delivery)


## 0.7.0 - 2026-10-03

### Added

- 新增默认关闭的标准 OTLP 可观测性：保留 PostgreSQL 日志，关联跨 Peer Job、AI 调用与结果来源，并提供浏览器认证转发。 (#observability-foundation)

### Fixed

- 修复 client-web#121 涉及的 Core 运行时问题：扩展冷恢复失败明确阻止就绪并公开失败的启动步骤；启动完成后立即扫描待执行任务，并在核对 Registry 精确关联后复用已安装扩展，减少重复 wheel 获取与安装。


## 0.6.2 - 2026-09-23

### Changed

- Core Peer configuration schemas now expose readable field titles without changing stored values.


## 0.6.1 - 2026-09-22

### Changed

- Publish first-party Extension documentation from saved candidates, and keep the official production deployment inactive in forks. (#extension-documentation)
- Extension 安装改为只确认 published Release；Python 兼容性和已加载版本保护在启用前执行。 (#extension-install-boundary)
- Publish each running Peer's application version with its runtime identity. (#peer-runtime-identity)


## 0.6.0 - 2026-09-21

### Added

- Core Host SDK 0.3 提供公共 HTTP 基址读取接口，服务包版本仍由独立发行流程管理。
- 支持通过 GitHub Actions 将自托管实例部署到 Heroku 与 Neon。

### Fixed

- 修复 Web 无法通过所选 Core Host 安装 Python-only Extension 的接入缺口，复用既有精确版本安装与兼容性校验，不改变 Extension Host SDK 契约。
- 修复深层 worktree 路径下开发数据库 SSH control socket 超长导致启动失败的问题，保留实例独立的 tunnel 与清理归属。


## 0.5.0 - 2026-09-20

### Added

- Add the asynchronous Agent Query Sink, owner-local Agent read tools, and Sink type discovery.

### Changed

- 生产发布摘要明确区分版本未变的空操作、完整发布与失败／取消，并记录源码版本、提交、候选镜像和 stable 更新结果；数据库边界检查复用 Ruff 的源码范围，避免旧构建产物导致误报。


## 0.4.0 - 2026-09-19

### Changed

- 数据库运行时操作使用原生异步 I/O 与显式 UoW 事务边界，覆盖 Graph、配置、AI/Agent、Peer、Source/Sink、Job/Cron、扩展采集、检索、Organization 和 MCP；批量写入 flat graph 与检索投影，数据库日志独立批量写入，关闭时排空任务后释放连接池。新增全 runtime 数据库边界检查和长期维护指南。 (#105)

### Removed

- Extension Host 数据库操作与配置／状态持久化接口改为异步，Host 合同推进到 0.2；旧同步 Extension wheel 必须先停用并升级至匹配的正式版本。
  删除运行时 SessionLocal、同步 BlockManager／RelationManager、可选 raw session 持久化及旧 Storage 写入接口；组合写入使用必需的 UoW，独立业务入口需要 await。 (#105)


## 0.3.0 - 2026-09-14

### Added

- 提供独立 CLI 使用的普通 REST 管理与检索接口、动态输入 schema 发现、原生二进制与 multipart 内容交付；Job 新增 best-effort 停止请求，显式 rumination 通过 Job 受理。减少已验证配置在内部传递时的重复校验。


## 0.2.0 - 2026-09-12

### Added

- Add extensible semantic Organization behaviors, bounded graph-use queries, and Human-reviewed
  acceptance fixtures, composable Agent read tools and opt-in development traces; also recognize schema-v3 SVC development
  database provider configuration. (#100)


## 0.1.5 - 2026-09-01

### Fixed

- Production delivery no longer waits for pull-request checks to reappear on a protected-main merge commit. (#production-main-verification)


## 0.1.4 - 2026-09-01

### Changed

- Protected-main releases now start directly from the admitted main push instead of rerunning pull-request CI first. (#main-release-orchestration)


## 0.1.3 - 2026-09-01

### Changed

- Release publishing now builds and validates its database schema from the exact protected-main source instead of consuming a CI artifact. (#org-repository-cleanup)


## 0.1.2 - 2026-08-30

### Changed

- Select normal Core production delivery through a prepared Core version instead of every checked main commit.
