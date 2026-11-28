# 交付、工具与运行边界账本

基线 `9f75cbb`；全部声明已完整读取，参数表及生产入口/CI/历史一并检查。无 DB 的 309 个 case 224 通过、85 跳过；一次性 PostgreSQL 基线 291 通过、5 失败、13 跳过。CLI 3 个声明独立通过，但 typecheck 在隐藏 worktree 路径中未发现源码而退出 1。这些不是待删“旧测试”。

## 逐声明决定

以下路径均相对仓库；R 保留，F 修复证明。同一行列出的声明各自具有所写独立契约，没有推测性删除。

| 文件、声明 | 决定 | 契约、可信回归及证明边界 |
| --- | --- | --- |
| test_dev_database_provider: remote_build_context_carries_the_sdk_and_required_license | R | 实际 tar payload 缺 Core 或许可证使远程构建失败；不是源码字符串库存。 |
| 同文件: compose_failure_keeps_both_streams_and_redacts_credentials | R | 命令失败输出保留真实错误并移除凭证；外层构建不便注入特定stderr。 |
| test_production_delivery_app_resolution: reads_exact_existing_app_pair | R | 仅读取被配置的两 App，不意外建应用。真实脚本+CLI外部响应。 |
| 同文件: fails_closed_on_auth_or_lookup_error | R | provider失败后有诊断、不创建，目标失败明确。 |
| 同文件: rejects_wrong_app_identity | F | 空/错误名称应拒绝；stub未写calls文件导致“无创建”断言总为真，补外部命令记录。 |
| test_production_image_transfer: pushes_only_the_exact_process_tags | F | 三种进程的精确源/目标镜像映射必须保留；改只验证外部transfer与pull契约，去掉私有inspect调用顺序锁定。 |
| 同文件: refuses_a_remote_tag_with_different_image_content | F | 远端错误digest阻止继续；以三种独立交付对象参数化，保留错误诊断，抵消上例顺序解耦后保护缺口。 |
| test_production_stopped_apps: existing_self_host_apps_must_be_stopped_before_migration | R | 正在运行的PostgREST阻止共享schema修改；不是“某物不存在”的无效负向。 |
| 同文件: empty_self_host_formations_are_ready_for_explicit_database_change | R | 真正空formation可继续，避免过度拦截。 |
| test_self_host_database_confirmation: rejects_database_mutation_without_explicit_confirmation | R | 未确认/false在实际validate入口拒绝，不输出可交付目标。 |
| 同文件: accepts_explicit_database_change_confirmation | R | 显式确认输出精确目标，负向例的有效正对照。 |
| test_production_maintenance: backup_branch_uses_exact_lsn_without_compute_or_expiry | R | 实际序列化Neon请求使用独立LSN，等待operation并验证返回分支/endpoint；mock不是恢复数据库证明。 |
| 同文件: reports_neon_error_without_exposing_request_credentials | R | 真实422诊断、隐去传入凭证；不当作真实Neon联通证明。 |
| 同文件: requires_capacity_for_backup_and_preserved_recovery | R | 容量不足必须在POST前拒绝，保证保留恢复分支的空间。 |
| test_production_preflight: database_inventory_is_read_only_and_distinguishes_legacy_pins | R | legacy/v2行投影差异与只读SQL是两个独立约束；mock只证明查询/报告拼装，不能声称真实快照一致性。 |
| 同文件: quiescence_gate_rejects_observed_peers_jobs_and_sessions | R | 三项冲突并存时不能丢诊断。 |
| 同文件: allows_only_core_lease_and_pending_jobs | R | 等待中的Job不阻止维护。 |
| 同文件: allows_idle_client_backends_without_transactions | R | 历史pooler故障的直接回归；与空sessions正例不同。 |
| 同文件: rejects_non_idle_or_unknown_database_sessions | R | 6行分别保护活跃、事务、未知状态；在owner函数直接判断真实风险。 |
| 同文件: tracks_real_postgresql_session_states | R | 真PG状态转换到同一gate；保留与上层policy table独立的PG语义风险。 |
| 同文件: flush_lsn_is_read_only_and_validates_the_database_value | R | LSN协议值与只读命令，invalid分支确到目标。 |
| 同文件: app_configuration_check_redacts_secrets_and_matches_roles_and_branch | R | 两传输角色、部署身份和secret安全投影，预期值独立。 |
| 同文件: rejects_wrong_database_branch | R | 故意改endpoint，其他输入合法，确到部署身份检查。 |
| 同文件: rejects_peer_id_outside_canonical_app_identity | R | 改peer ID，其他合法，与错误DB不是同一条件。 |
| 同文件: checked_in_production_profile_is_reported_as_declared_source_data | R | 确保实际保护部署profile可投影；不将声明当成线上状态。 |
| scripts/tests/test_extension_documentation: documentation_targets_the_saved_native_release | R | prepare→publish→verify重载候选，环境后续变化不漂移原生发行目标；Python/module_federation具有不同文档范围。 |
| scripts/tests/test_preview_delivery: failed_migration_keeps_observed_heads_and_reports_partial_delivery | R | 实际脚本失败17保留前后head并报告部分成功，secret不泄露；不是实际provider迁移验收。 |
| 同文件: preview_skips_only_a_complete_stable_tests_only_diff | R | 完整分页、改头/基线、rename、API失败九行独立选择条件，缺资料应执行preview。 |
| chatgpt/test_lifecycle: waits_for_core_and_sigterm_stops_both | R | 真子进程+HTTP readiness+SIGTERM；无live tunnel凭证。 |
| 同文件: tunnel_crash_restarts_only_tunnel | R | 真SIGKILL后恢复子进程，Core身份不变；不是调用次数模拟。 |
| 同文件: core_crash_stops_tunnel_and_exits | R | 主进程死亡触发子进程清理及137状态。 |
| 同文件: credentials_are_env_references_and_target_is_local | R | spawn命令不含secret字节且localhost sink正确；这是进程边界参数合同，保留合理接缝。 |

## 层级与执行计划

- 没有确认可整套删除的交付契约。保留同文件owner，只修两处断言；用三种镜像错误内容对照证明保留保护。
- 默认收集纳入 `scripts/tests` 和 ChatGPT 生命周期，删除 CI 对已进入默认gate的两个重复命令；协议数据库测试在 DB lane 正式准入。
- `pdm test` 排除 acceptance；真实外部验收明确标记。数据库lane要求显式测试URL、迁移URL、一次性集群admin和原有角色密码。恢复迁移suite实际执行，不能把skip当保护。
- CLI typecheck指定项目排除，防止旧Pyrefly把整个隐藏worktree源码排除。已用临时错误赋值得到真实 bad-assignment / exit1，移除控制文件后再检查。
- 本机svc schema不兼容不改变项目schema；直接用现有脚本初始化本任务容器并通过任务专用SSH转发。保留主/旧审计工作树。

## 剩余证明边界

Shell外部I/O替身保护组装和失败控制，真实镜像/PostgREST/恢复由现有 artifact CI owner承担；真实Neon、Heroku、用户设备和付费AI不从本地mock绿灯推断。本轮产品schema变更必须让 exact-head artifact CI执行新库初始化、导出及重新恢复。

## 已完成的补证

- 镜像传输 keeper 不再锁定 inspect 调用顺序，三种进程各有错误 digest 拒绝对照；故意移除 digest 拒绝后 3 个 case 均失败，恢复后通过。
- 错误 App 身份的 fake CLI 现在实际记录外部命令，移除始终为真的“文件不存在或没创建”条件。
- 发布恢复的旧测试没有证明保存文件恢复。改为按 YAML 的可执行命令识别 capture/publish/discover，保留平台调度条件和下载唯一 artifact 的独立静态契约，允许 job/step/id 重命名。新增实际 save→复制→删除原始输入→restore 的 release/no-op 两分支；独立 SHA-256 oracle、篡改拒绝与还原通过。删掉校验语句的 control 因目标篡改被接受而失败。无网络或真正发布。
- PyYAML 原本随 dev pre-commit 安装，现显式声明为该静态边界的开发依赖并补类型 stub。原依赖版本无变化。
- 发布／App／镜像三个文件最终 11 passed；更晚的 YAML discover 条件保留经完整默认 gate 验证。默认入口 228 passed、89 DB/环境 skips、6 explicit acceptance deselected；数据库 gate 86 passed、0 skipped、1 credentialed deselected。后续 Peer HTTP 2 个新 case 的证据归 execution ledger，整合检查状态归 verification.md。

## 首轮镜像验收发现的清理缺陷

PR #201 的首轮 CI（`45998528255`，实现提交 `d60bc6c`）通过仓库、core/CLI/第一方扩展 wheel 和数据库 keeper，但 artifact 的 PostgREST 探针失败。`scripts/verify_postgrest_contract.py::verify` 写入非空 state 后直接 DELETE 作为清理，与恢复的数据库约束冲突。此处先前保留判断遗漏了探针与新契约的关联；不能通过撤下 trigger 或忽略 DELETE 失败解决。

修正该既有验收 owner：验证非空 state DELETE 返回 HTTP 400 且 SQLSTATE 为 `23514`、错误消息对应 state guard；再清空探针自己创建的 state，确认被拒删记录仍存在，最后允许 DELETE。探针开始时也只清空固定探针身份的遗留 state，允许失败后重新运行。

本任务一次性 PG17 + pinned PostgREST v14.15 上，旧探针确实报 `Extension cleanup returned HTTP 400, expected 204`。新探针先从该遗留现场恢复，再完整运行第二次，均通过全部 HTTP checks。临时禁用本任务数据库的 trigger 后，新探针准确失败于 `non-empty state delete returned HTTP 204, expected 400`；finally 恢复 trigger 后再次通过。格式、lint 和类型检查通过。后续完整镜像运行以 PR 的当前 CI 为准，未以这一直接 HTTP 证明替代镜像、reset 和导出恢复。

修订提交 `2922000` 的运行 `45999523367` 已完整通过：仓库/core/CLI/扩展构建、PostgREST 状态删除约束与清理、两次 reset、Web ready，以及新库导出恢复。独立只读复核无阻塞问题；固定探针身份不能并发复用这一既有边界未变化。

## 6096624 重审的只读盘点与层计划

下列决定在实现前核对，修订前表。基线逐文件结果来自同一 SHA 的 CI 及原始 pytest XML。CI 以实际调用支路认定归属，工作流名不能代替对执行体的检查。

| 增补声明／原决定 | 发现、决定与保留证明 |
| --- | --- |
| test_extension_distribution::test_metadata_targets_only_the_exact_python_distribution | C。对 `read_project().prepare_document()` 的断言和 CLI 调用重复，后者又从生产函数取期望。发行和 preview 消费的是 CLI 的最终 JSON，将完整预期移到唯一 CLI 断言。私有组装有生产调用，保留。 |
| test_extension_publication_recovery::test_workflow_rejects_recapture_and_downloads_the_unique_saved_artifact | R。Actions 调度复跑、成功依赖与唯一 artifact ID 无法从本地 restore 推断。保留平台表达式和执行 guard。 |
| 同文件 test_candidate_recovery_uses_saved_bytes_after_original_inputs_disappear（unchanged、release） | R。两分支均实际放弃原目录从下载副本重开，校验返回的字节。 |
| test_production_preflight::test_database_inventory_is_read_only_and_distinguishes_legacy_pins | F。SnapshotCursor 根据私有 SQL 片段直接供应最终行，删除 WHERE 或写坏 SQL 仍可通过。“只读”允许任意 SELECT 和 SET 前缀。改由历史和当前 PostgreSQL 实表及实际事务观察；滚动迁移保留精确 pin 的契约独立于预检报告。 |
| test_quiescence_gate_tracks_real_postgresql_session_states | R。既有 PG 系统视图状态检查和危险状态策略表保护不同错误；全空和 idle pooler 保留。 |
| test_flush_lsn_is_read_only_and_validates_the_database_value | F。私有 SQL 两字符串锁定；改为承认 PostgreSQL 的 pg_lsn 类型，由实际连接核对 flush 位置与服务端只读事务。格式无效应在外部 Docker 输出流入 Neon API 的边界拒绝。 |
| test_production_delivery_app_resolution 三声明 | C fixture。三者均模拟同一 Heroku 可观察边界，错误名字脚本重复建假 CLI，合并 fixture 并保留实参记录和各自错误。 |
| scripts/tests/test_preview_delivery::test_failed_migration_keeps_observed_heads_and_reports_partial_delivery | F。前后响应完全相同，脚本复用旧报告仍绿。故障后的独立 head、probe exit、已跳过的发布和现存 release 应分别出现。 |
| scripts/tests/test_extension_documentation::test_documentation_targets_the_saved_native_release（两行） | F。fake 用单一 published 标记后枚举所有本地候选，一次发布等于所有 scope 已发布。改为独立已发布记录，每次只接受传给 provider 的候选，验证已发布目标/URL，解除 show 次数约束。 |
| ChatGPT 四声明 | F 已就绪与凭据两例。仅 sleep 不能证明 Core 曾返回拒绝；负向等待以实际 /readyz 响应为信号。现有 argv 验证只证明 secret 未以内联值出现，漏掉未传引用的情况。另两条保留真信号与进程组。重试/poll 参数用于有界故障注入，有独立价值。 |
| 镜像 reset 两次 | F。无写入的干净基线可使整个 reset 成为 no-op；唯一生产函数 development_baseline_fingerprint 仅供 CI。把七类确定性记录比较移至验收侧，通过真实写入干扰并重新连接来观察清理，保留双 reset 契约及开发环境护栏。 |
| PostgREST JWT 生成、其他开发参数 | D：私有 _token(now=...) 的时钟参数全仓无传值者。R：SSH __provider-check__ 是 dev provider 的运行诊断，文档和 release 的 CANDIDATE_ROOT 由工作流交接 artifact，ChatGPT 的健康文件用于实际客户端，不能据测试覆盖而删除。 |

可执行与手工验收该轮的责任边界如下。R 条目通过真实 owner 的现有验证复用，F 条目实施在上述 keeper。

- R — CI repository 的 lock、Ruff/类型、架构依赖和 AST 别名自检、Alembic graph 与跨 base 完整性、release intent、核心 wheel 独立安装/无 app 导入与全部第一方 wheel 元数据和入口。它们检查独立可安装性、依赖边界、部署历史，不重复 Python 导入测试。CLI wheel 所属子域另列。
- R/F — artifact 的 runtime 文件/运行用户、重复初始化、数据库/元数据 ready、PostgREST JWT 认证及状态 CRUD、开发 reset(F)、真进程 liveness/readiness、runtime 库导出、角色 SQL 与 schema 在另一 PG 集群还原再 init/ready。重复初始化、重置、还原分别依赖不同先前状态。
- R — preview 的可信 PR/head 和 required checks、完整文件差异判定、Neon 父分支／到期、建候选 wheel 和 registry Pages、建 transport、一次性的数据库变更与前后事实(F)、双 app 和 release、Peer 广告、线上 HTTP/JWT 探测、部分部署摘要和限定身份清理。外部 probe 的权限和状态归平台。
- R — artifact publish 的 exact-main、schema 证据重算、commit 标签、镜像内 schema、内容摘要与 main 别名；production deploy 的版本选择、既有 app/branch 身份、pull/transfer/verify、广告、健康探测与 stable；maintenance 的两次 inventory、停止进程、观测的会话/租约/job、备份容量与精确 LSN、远端 operation 完结、无 compute 或过期、文件与恢复手册。回滚、恢复分支与真实外部停止不能从 curl fake 推断。
- R — extension-publish 的发现/历史、选择/build/finalize、唯一 artifact 保存与恢复、候选重验、同一版本 Python Distribution prepare/publish/verify；extension-documentation-publish 的页面范围、发布前源复验、乐观 etag、保存的精确版本/分发及公开快照回读(F)；release-pr 和 cli-publish 的 base/版本/打包与条件发布归发布工作流。
- R — self-host Heroku 的显式数据库确认、目标 app 身份与停止、首次和重复收敛、按目标 probe；Render 的 branch 身份、凭据重用、exact commit deploy、Core/PostgREST 双 HTTP probe；Render 的 fork dispatch 授权执行收敛，独立的显式数据库确认属于 Heroku。Heroku/Render fork onboarding/cleanup、Neon 的线上备份回滚手册/隔离及同机 docker compose/SSH diagnose 属实操场景，线上资源未调度。
- R — ChatGPT 的固定 client 校验、单实例配置/preflight、启动/重启/关停、当前 dyno 才能就绪、真实 PAT initialize 和个人插件已知内容读回。pytest 负责进程和 argv/env；外侧 provider/个人插件另有入口，未以日志代替用户读回。

### 切换与验证结果

- 纯数据库游标 mock 及按查询文本返行的分支已删除。迁移 fixture 在真实 `3d9593b0c855` 和 head 各取得一次库存，核对同一旧 pin、带明确分发的禁用 pin、运行与已结束 Job 的区分及 PostgreSQL 正在使用的只读可重复读事务。LSN 实际位于同一 PG 观测窗口内，查询时从服务端确认只读；Docker 输出格式错误在首个 Neon 请求前被拒。已核实旧 preflight 数据库环境变量只有 CI 与该测试，统一使用一次性 PG admin URL。
- 发行 metadata 只保留完整 CLI 输出的一份独立 JSON 预期。Heroku 三个 App 身份断言共享外部 CLI；进程镜像保留显式来源/目的地及错误 digest，取消无契约依据的 tag 先后顺序。Core 与 PostgREST 任一仍在运行都拒绝迁移，缺少自部署确认的行不再意外继承父进程开关。
- 文档发布 fixture 先有目标 global 记录与旧 etag。每次只保存真正提交的 scope，按目标版本、分发和坐标查询，自独立 registry 观察已发布快照；verify 切换环境版本后仍须访问候选的所有 URL。把第二次发布替换成 global 后，Python 分发的 keeper 对遗漏 scope 变红。Preview 迁移前后独立返回两个 head 和退出码，再从失败摘要读回。ChatGPT lifecycle 改为观察 Core 两次真实的未就绪响应，并核对 Tunnel 实际依赖的引用参数；绕过 readiness 的控制失败。无调用者的 stop grace 参数已删除；子进程失效重启的可控延迟保留。
- Render 的 `base_url` 从未有覆盖值；`RenderClient` 仅从自部署入口构造，无外部 SDK 导出。删除该参数，保留传输接缝，使测试在真实 HTTP 请求序列化后拦截外部服务。历史 `60d9e61` 随全套清理移除了 370 行。本次恢复非幂等的环境整表写回和新建服务自动部署错误 commit 两类独立操作风险。只读第一页、接受错误 replacement commit 两个控制均失败。前者检测丢失运营方在第二页保留的值，后者直接检测“未抛出指定部署错误”。
- reset 的七类稳定字段改由验收方 SQL 独立读取，与生产 catalog API 解耦。真实 `redo_delivery` 的基线摘要为 `ac085491…`；插入测试 Extension 后为 `407b5045…`，空操作无法通过。两次正式 reset 后均恢复前一摘要。删除仅供 CI 的 73 行指纹函数及其两个 import；取证的 psql 和 SQL 位于 CI/测试侧。
- restore 原流程能够把两个空 SQL 文件的结果初始化为可用数据库，随后 `ready` 返回成功。临时本机 PostgreSQL 17 的空集群重现了该假阳性。新增初始化前的角色回导比较和 `alembic check`，同一空现场分别确因缺少角色、数据库未达迁移 head 而失败。在第二个全新集群真正导入角色、schema 和迁移元数据后，这两个检查以及 init/ready 均通过。本机导入来自一次性 development 库；CI 另外覆盖中性 runtime profile、真实镜像和 PG17 导出恢复。两个本机临时集群均已关闭清理。
- PostgREST 的 authenticated read/write、非对象 state、身份不可改、匿名失败、旧可变字段/RPC 被拒、Peer participation/readback、新 state 删除约束及清理、wrong secret 均属于外部 HTTP/DB 协议或状态保护。保留原脚本；两次调用 JWT 构造器均取实时时刻，移除从未传入过的 now 参数。其余 production/preview、PG 数据库快照恢复手册和 ChatGPT 个人插件验收仍适用上表的真实环境边界。

领域聚焦选择（含历史迁移）在一次性 PostgreSQL 上 58 passed，32.38 秒；文档 fixture 加入既有 etag 后 2 passed，1.19 秒。Ruff、Pyrefly 和 shell 语法通过，独立副本 4 次变异各因对应契约变红。脚本及线上凭证隔离，未发起实际提供方发布或维护操作。精确头的容器编排验证由本 PR 的 artifact gate 执行；全仓行数及阶段盘点归总验证账本。

### 独立保留复核后的补证

- 开发重置有两道独立保护。一次性 PostgreSQL 17 的 development 库收到错误确认词后返回确认词错误，含哨兵的完整摘要保持一致。恢复出来的 runtime 库收到正确确认词后返回身份错误，重连仍可读到哨兵。两个变异分别移除确认词判断和清空前的身份判断。前者让错误请求成功，后者清空数据之后仍因 seed 的身份检查返回相同错误。拒绝原因和事后读回都进入镜像验收。恢复原实现后，两次合法重置均回到初始摘要。
- 用两个只监听临时 Unix socket 的 PG17 集群完成中性 runtime 制品导出及新库导入。一次扩大 data-only 导出的表选择，带入 `inkcre.job_types` 行。另一次在导出前写入过时的 contract revision。原有导入、角色对比、Alembic、init、ready 对两份制品全部成功。新增的初始化前只读 SQL 分别指出首个含数据的协议表和错误的合同状态。干净制品通过同一 SQL，初始化后 ready 成功。两套临时集群已停止并回收。本地日志为 `redo-delivery-preservation.log`，Docker 命令封装由 PR 制品 CI 验证。
