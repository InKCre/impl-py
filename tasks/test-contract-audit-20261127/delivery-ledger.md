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
