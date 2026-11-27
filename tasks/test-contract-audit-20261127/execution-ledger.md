# 执行、实例、扩展参与与观测契约审计

基点 `9f75cbb`。已完整阅读本 lane 的 15 个文件、53 个测试声明及参数表、fixtures、相关生产 owner、调用方、CI 和提交历史。全仓默认／显式 hermetic 基线来自 `baseline.xml`（224 passed / 85 skipped）。下表记录原始审计判断；实施结果与验证见文末。标记 R 保留，F 保留契约并修复，C 合并至指定 owner，D 删除；无独立删除依据时不删。

## 结论与优先顺序

1. **P1 / test-adequacy：迁移与 ACL suite 被静默跳过且已经失修。** `test_extension_peer_enabled_rpc.py` 只找本机 `initdb/pg_ctl/postgres`，不使用 CI 已提供的一次性数据库；`repository_check.sh database-tests` 未选它。`postgres_connection_info` 含 `dbname`，随后 `psycopg.connect(**info, dbname=...)` 会触发重复关键字；业务用例 monkeypatch 的 `AsyncSessionFactory` 已在 SDK 抽取时删除，真实 UoW 读取 `current_core().sessions`。`4fa74bb` 的该文件 diff 只改 import，证实不是待支持的旧接口。保留六个独立契约，改到显式一次性 admin URL、测试自建 database 和 `Core.bind()`，接入 required database lane。不得改共享容器角色密码。
2. **P1 / test-hygiene + test-adequacy：观测 PostgreSQL 用例没有经过写入或真实筛选。** 固定 `MagicMock` 返回一行；SQL 中 WHERE 先于 LIMIT 是语法形状，不能拒绝漏筛选、先截断后筛选或落盘丢字段的实现。替换成 backend 实际写入、关闭/新建 reader、独立历史行、非匹配干扰行和截断断言。Loki 保留，负责独立 HTTP/LogQL 契约。
3. **P2 / test-adequacy：Job/Cron 的非正常执行边界不足。** 全仓查找可见 RSS 用真实 PostgreSQL 和 HTTP 计数证明并发双 run 只领取一次；其他 suite 证明 run_now→Job→结果。未发现 `abort/check_abort_requests/expire_overdue` 或 `CronManager.check` 的维护证明。应针对暂停清理期间仍为 running、跨实例持久终态、同一分钟最多一次 occurrence 建立 owner 证据，不重复已有 RSS claim 断言。
4. **P2 / test-hygiene：Agent snapshot 名称大于实际证明。** 当前 definition 用例创建 Thread 后没有修改 definition/工具，无法区分 snapshot 与动态引用；在原用例增加旧 Thread 与新 Thread 对照即可。AI/Agent 的 IntegrityError 缺少数据库约束诊断，需排除无关前置错误。
5. **P2 / test-hygiene：空 selection 用例名称错误。** `test_startup_provision_does_not_touch_database_when_no_pins` 实际总会调用 store.list_participations，且没有断言；真正应守的是无 enabled pin 不解析 Registry、不执行 package acquisition，可与 selection 参数表合并。

## 所有声明的判断

下表名称均指各文件中的完整测试声明；同一参数表的各行具有相同归属，表中说明参数差异。

| 文件／声明 | 标记 | 独立契约、可信回归与剩余证明 |
| --- | --- | --- |
| `test_core_context.py::test_concurrent_instances_keep_their_identity_and_restore_nested_scope` | R | 同时绑定两信息库和嵌套恢复；全局 singleton 或 ContextVar reset 漏掉将失败。sleep(0) 是确定性让出，不是时间猜测。 |
| `test_borrowed_engine_is_not_disposed_and_options_are_explicit` | R | SDK 借用 engine 不取得关闭权；dispose 次数是资源所有权契约，不能由 HTTP E2E 替代。 |
| `test_owned_engine_is_disposed_once` | R | 自建 engine 关闭幂等；与 borrowed 风险不同。 |
| `test_legacy_imports_share_model_and_service_identity` | R | README 明确仍支持旧迁移／已发布扩展导入；类同一性保护 SQLModel/registry 兼容，非“旧代码必须消失”。 |
| `test_closing_one_core_drains_only_its_pending_work` | R | 一个 Core 关闭不取消另一个实例任务；尚不能证明数据库 Job abort 终态，需另证独立风险。 |
| `test_asgi_binding_covers_lifespan_and_requests` | R | App middleware 覆盖 lifespan/http 两个调用上下文；独立于 SDK 绑定语义。 |
| `test_job_execution_keeps_logging_when_woken_by_a_quiet_poll` | R | quiet polling ContextVar 不吞 job execution 日志并恢复 caller；fixture 替换 worker body 以观察执行入口上下文，未模拟目标行为。 |
| `test_extension_registry_config.py::test_registry_origin_rejects_non_origin_values` | R | ftp、userinfo、路径、query、fragment 各有入口规范风险；match 确认到达 origin guard。 |
| `test_registry_origin_normalizes_one_trailing_slash` | R | registry origin 标准化是 Host/Registry URL 合同。可并入同 owner 参数表，但不足以单独删除。 |
| `test_executing_peer_registry_override_wins` | R | 执行 peer 配置优先，fallback 访问抛错检测不必要读取；fake 只供配置。 |
| `test_deployment_registry_default_precedes_process_fallback` | R | 部署 config 优先于进程 fallback；与 peer 分支独立。 |
| `test_process_registry_origin_is_the_final_fallback` | F | 最终 fallback 的预期调用被测 normalize；应 monkeypatch 固定 process URL、期望固定 origin，避免共同错误。 |
| `test_deployment_registry_contract_is_strict` | R | 保存入口拒绝 unknown key、标准化 URL；建议负向限定 extra_forbidden，不因 Pydantic 标签删。 |
| `test_extension_distribution.py::test_metadata_targets_only_the_exact_python_distribution` | F | CLI metadata/Registry prepare JSON 有独立 wire 契约；group 期望目前引用同一常量，应固定协议字面值。第二次 CLI 比较同 prepare 可保留，因为第一次已有独立完整 oracle。 |
| `test_extension_peer_status.py::test_shared_protocol_preserves_a_distribution_unknown_to_python` | R | 非 Python peer 可经过 Python DTO 保留自己的 distribution；SDK/wire 准入独立于数据库保存。 |
| `test_status_reports_peer_capabilities_before_and_after_shared_install` | R | 空安装／共享已安装且本 peer 无 selection 仍报告 Python 能力；两个参数行保留。 |
| `test_extension_publication_recovery.py::test_full_rerun_fails_capture_and_publish_only_rerun_restores_saved_artifact` | F | 防止 rerun 重建候选有真实发布风险；当前源码 split 锁定 job/step 名称，仅执行拒绝脚本，未实际证明 saved artifact 恢复。保留拒绝保护，需把恢复选择行为移到可执行 release owner 检查；不可直接声称“round-trip恢复”。 |
| `test_extension_participation_lifecycle_races.py::test_late_startup_candidate_is_discarded_after_intent_is_disabled` | R | enable 与 cold restore 在初始化 await 期间 intent 被撤销，必须释放候选/claim；参数两行对应不同入口。fake start 是确定性竞态接缝，保留。 |
| `test_extension_startup_provision.py::test_startup_provisions_only_enabled_exact_python_selections` | F | 已解析／legacy pin 两行均保持精确版本且忽略 disabled；`resolver.lookups` 恰好两次锁定内部验证次数，应验证所有 lookup 都是期望 exact pin，加 package application 结果。 |
| `test_startup_stops_when_exact_enabled_pin_changes_during_restore` | R | download/apply 两阶段不同风险：未应用前中止、应用后禁止继续；错误 match 和已保存 replacement 都有效。 |
| `test_cold_restore_resolves_only_the_exact_enabled_legacy_pin` | R | Host 需要调用正确 legacy resolution 再启动；SQL 原子转换另由 migration suite 守，不互相替代。 |
| `test_startup_rejects_enabled_module_federation_without_mutation` | R | Python Host 拒绝无能力 distribution 且不删 intent；match 精确 unsupported 值。 |
| `test_startup_downloads_multiple_pins_into_isolated_directories` | R | 不同 wheel 下载目录隔离；package consumer 的目录契约，合并用户层 suite 会损失诊断。 |
| `test_container_web_provisions_before_exec_and_keeps_ready_read_only` | F | ready→provision→exec 是必须的 bootstrap 顺序，保留；当前只调用 _web，名称中“ready read-only”未通过单独 ready 命令验证，应改名或补真实入口证据。 |
| `test_startup_provision_does_not_touch_database_when_no_pins` | C | 当前无断言且名称不实。并到 enabled selection 参数表，观察 no registry/acquisition，同时保留空选择与 disabled-only 条件。 |
| `test_environment_application_rejects_core_runtime_or_base_downgrade` | R | 共享 Python env 中 runtime 被换掉即拒绝继续；fake distribution snapshot 是 package 边界，不需真实 pip downgrade。名称应只说已覆盖 runtime downgrade，或扩展独立核心/基础依赖行。 |
| `agent/test_debug_trace.py::test_trace_retains_tool_errors_and_actual_budget_outcome` | R | 预算1/2、输入校验和 handler异常均经真实 Turn，输出诊断对应实际结果；trace记录不替代 ToolResult持久证据。 |
| `test_trace_records_cancellation_without_turn_recovery` | R | 真实任务取消后产出 cancelled/model_calls；Event 协调保留，不换 sleep。 |
| `test_debug_disabled_and_broken_destination_do_not_raise` | R | debug 关闭／不可序列化输入／sink失败不能破坏业务；无异常是该边界的实际成功结果。 |
| `agent/integration/test_agent_definition.py::test_agent_definition_round_trip_and_active_thread_snapshot` | F | 数据库 definition→AgentManager→Thread 路径有效；补创建后修改与旧/新 snapshot 对照，限制 check violation 诊断；时间戳由 configs 另守不同表触发器，不能按重复标签删除。 |
| `ai/integration/test_ai_registry.py::test_ai_facts_round_trip_typed_capabilities_and_database_invariants` | F | AI capability JSON round-trip、模型身份不可变与 profile 时间戳是独立 DB 契约；IntegrityError 应确认 immutable trigger，而非任意数据库失败。 |
| `deployment_config/integration/test_config_resource.py::test_config_resource_replace_patch_read_and_explicit_failures` | R | HTTP replace/patch/delete，正常读取不重验已有 invalid/unknown schema；写入经真实 API+SQL，区别 accepting/read authority。 |
| `test_database_owned_timestamps_ignore_no_op_and_observe_row_changes` | R | configs/blocks/relations 的 no-op 与真实修改是数据库-owned timestamps；分别有独立表触发器风险，不删。 |
| `peer/integration/test_peer_runtime.py::test_real_snapshot_database_lease_and_candidate_filtering` | R | 保留 Human config/name、刷新 app version、数据库时钟 lease和候选选择；private candidate不是实现重演，它直接决定发送对象。尚不证明真实传输 failover。 |
| `test_failed_cold_restore_preserves_intent_and_reports_not_ready` | R | 真 App lifespan+不可达Registry→ready503/live200，无lease且intent仍在DB；与 Host fake race/distribution suite 保留各自风险。 |
| `migrations/test_extension_peer_enabled_rpc.py::test_upgrade_preserves_shared_state_and_exact_unresolved_legacy_pin` | F | 真实 old revision→head，历史config/state/selection不丢；修 fixture重复dbname与执行路由。 |
| `test_enabled_legacy_resolution_preserves_exact_pin_and_null_schema` | F | SQL legacy转换CAS、原版本及历史snapshot保留；改Core.bind、初始state必须遵守 insert-empty后update，不改变数据合同。 |
| `test_v2_acl_and_state_guards` | F | authenticated/core/anonymous 权限、state object、禁止非空state删除；当前旧的 AsyncSessionFactory、陈旧state期望和过宽ProgrammingError需要修，逐一核对实际SQLSTATE。 |
| `test_peer_can_persist_its_own_distribution_format` | F | 原生未知distribution持久化+enable；修Core绑定，保留区别于DTO准入的DB风险。 |
| `test_selection_change_clears_schema_and_rejects_late_old_callback` | F | 切版本清schema、旧callback不能覆盖新选择；修Core绑定，已有匹配诊断与重读正确。 |
| `test_setup_source_and_cron_use_simple_core_owned_operations` | F | 正式Source→Cron→两个手动Job持久化；修Core绑定，并改成契约名称；不能作为Cron定时去重证据。 |
| `migrations/test_memos_attachment_v2_migration.py::test_seeded_v1_upgrade_and_downgrade_preserve_identity_blob_and_owner_slot` | R | 历史行升级→真实下载→降级→重开DB、原blob和关系保留，独立可信恢复路径。 |
| `test_failed_downgrade_preserves_shared_semantic_content` | R | shared semantic新增信息阻止有损降级，精确原因、跨事务全行/关系/blob比较；负向 absence 断言有保护价值。 |
| `test_observability_records.py::test_record_and_query_reject_unbounded_or_ambiguous_input` | F | 外部请求大小/时间/union规范有效；每个 ValueError 应带目标校验诊断，避免前置失败冒充。 |
| `test_loki_roundtrip_encodes_filters_and_does_not_leak_auth` | F | 固定ns、LogQL转义、truncation和借用client寿命保留；写入回显作为reader fixture会共同漏字段，补独立协议JSON；标题auth未注入凭据也无assert，应补或收窄名称。 |
| `test_external_failure_is_not_empty_or_a_database_fallback` | R | 500返回不能视空/回落DB，query失败类型及去敏、丢弃数均可观测。 |
| `test_metadata_and_instance_lifetimes_do_not_need_otlp` | R | 事件/usage/operation归正确Core，无OTLP仍可记录且关闭只影响该实例；mock仅存输出。 |
| `test_postgresql_preserves_legacy_job_association_and_filters_before_limit` | F | 移至真实PostgreSQL持久契约，删除旧 SQL shape/mock 行；新keeper应覆盖三种job关联、literal通配符、筛选/排序/limit+1和重开读取。 |
| `test_route_refuses_stale_backend_and_hides_invalid_body` | R | 真实HTTP409/422/413是路由接受边界；与record validator独立。 |
| `test_failed_metadata_and_malformed_logging_keep_business_semantics` | R | 业务错误不被telemetry吞掉/替换，不合法日志丢弃且计数；mock收集真实runtime输出。 |
| `test_public_selector_is_authority_and_failed_external_config_never_defaults` | R | 显式loki失配/读取配置失败不可静默写DB；调用键属于public deployment config，保留。 |
| `test_optional_otlp_connection_belongs_to_bound_core` | R | OTLP export连接隔离与关闭权；独立于observability记录backend。 |
| `test_capability_bounds_slow_body_and_concurrent_requests` | F | body timeout到达真实guard，必要确定性短deadline接缝；并发部分直接改counter只证明predicate，改为4个实际悬挂请求+第5个429，释放后可继续才证明生命周期。 |

## 第二遍层级计划与剩余证明

- **迁移／ACL keeper**：保留现文件六个声明；只改环境fixture与Core调用边界、陈旧初始数据、精确负向诊断。重用测试指定 admin 创建随机数据库，任何角色创建/密码变更由隔离容器初始化 owner负责。先跑基线确认失败原因，修后跑同一suite；不把环境失败说成产品回归。
- **观测 keeper**：Loki测试仍归hermetic协议；PostgreSQL写入/历史记录/查询移至 `tests/observability/integration/` 并由 required DB命令选取。唯一删除对象是旧mock SQL测试，先建立新keeper并证明删掉job OR或literal escaping会失败。新增测试没有生产接口。
- **低维护成本修正**：配置fallback和metadata固定独立期望；观测negative精确match；startup空/disabled合并到参数表，去掉固定resolve次数，不去掉download/apply次数（有外部副作用意义）。
- **缺口 keeper**：Agent snapshot增补原用例；Job abort/Cron scheduled occurrence需要独立owner测试，不能以现有普通run_now或RSS claim替代。Peer failover的 pre-dispatch/not-executed/outcome-unknown 边界未在本lane看到；应由主线程在其他lane/脚本核实，若无再建立HTTP边界证据。
- **发布工作流**：源码解析测试受step名称耦合，完整saved artifact恢复没有被证明。主线程持有工具链owner，需结合 `scripts/automation/extension_publication.sh` 现有集成方式决策，不能在本lane无证据删除。

本次没有可以直接全部删除的整文件。支持代码均有当前有效keeper，未发现可安全移除的production-only-for-test出口。保留内部算法/状态/注入接缝不等于接受其全部现有断言。

## 已实施结果与验证

**实际产品缺陷：Extension state guard trigger 丢失。** `e6a1b2c3d4f5` 删除旧 trigger 后仅重建函数，没有重新挂载。新建当前head数据库的 `pg_trigger` 查询确认 `inkcre.extensions` 没有非内部trigger；恢复可执行测试后，非空state的DELETE没有抛错。新增 `c7f2a6d9e410_restore_extension_state_guard.py`，parent `bb5dcb562587`，重新挂载同一函数；没有改任何已部署revision或已有行。`revision-integrity.json` 通过正式 `pdm run db:record` 追加。部署说明和core fragment同步维护。

迁移suite改为显式 `INKCRE_TEST_POSTGRES_ADMIN_URL`，随机创建／删除自己拥有的数据库，通过同一集群既有密码执行正式 provision-roles，业务操作绑定独立Core并在同一Runner释放。旧 `AsyncSessionFactory`、重复dbname、缺少peer config_schema、FK flush顺序及陈旧state期望已经修正。负向断言验证CheckViolation/InsufficientPrivilege及目标诊断。删除的两个“已移除version列更新报错”子断言只依赖历史列消失，其正式schema/metadata gate仍保留；未删除state/ACL契约。

- `execution-migration-broken-fixture.log`：仅让原suite使用显式一次性cluster，**5 failed / 1 error**，记录真实harness失修；不算产品回归。
- `execution-state-guard-before.log`：修正harness、未修产品时，**1 failed / 5 passed**；唯一失败是 `state is not empty` 保护的 `DID NOT RAISE IntegrityError`。
- `execution-after.log`：恢复trigger后迁移、Job/Cron、观测、Agent/AI共 **12 passed / 28.03s**。
- `execution-final.log`：所有本lane已修改owner测试共 **43 passed / 35.61s**，只有第三方 Starlette/AnyIO deprecation warning。
- 最后的fixture类型清理后，Job/观测/Agent **5 passed / 4.08s**；最后的HTTP动作断言后，hermetic观测 **9 passed / 0.62s**。
- 针对全部本lane修改文件，Ruff检查、格式检查、Pyrefly（含warning）通过，**0 diagnostics**；`git diff --check` 和migration integrity通过。

观测PostgreSQL keeper移动到 `tests/observability/integration/test_postgresql_records.py`，真实writer关闭后新reader读取；包含三种job关联、独立旧行、精确literal文本、过滤先于limit、现代字段和legacy nested extras。交叉审查发现旧mock的legacy `pg.<DBID>`／`event_name=None` 保护被漏搬，已按实际插入ID补齐并在两次query核对身份稳定。四个进程内生产query变异分别被keeper拒绝：移除job过滤（0.60s）、移除text过滤（0.56s）、错误legacy ID命名空间（0.64s）、伪造legacy event_name（0.47s）；源文件没有临时改写。最后正常keeper **1 passed / 0.59s**。对应日志为 `observation-*-control.log` 和 `observation-final.log`。

新 `tests/job/integration/test_execution_lifecycle.py` 使用真实DB、两个Core和Event协调证明：abort intent持久化但cleanup完成前状态仍running，重复abort不打断清理、不改终态；pending可立即aborted；Cron并发同分钟只建一个Job、上一Job未终结不新建、手动run不改定时进度；已消失executor的过期running由数据库时钟关闭。未重复RSS已有单次领取证明。clock seam仅固定Cron observation，未模拟持久化、锁或去重。

Agent原用例在首个模型调用悬挂期间实际更新definition，再对比旧Thread refresh后snapshot与新Thread；事件在断言前发出且等待有上限，错误实现不会无限挂住。AI/Agent DB负向分别锁定identity immutable与正预算constraint。其他F/C已完成：固定Registry fallback/entry-point wire oracle；空/disabled startup合并参数表并断言无Registry/acquisition，去掉内部lookup次数；Loki独立JSON输入/输出和真实HTTP动作、凭据不入payload；观测并发边界由4个真实悬挂请求占满再验证第5个429及取消释放。

本lane原始test+fixture **2,974行/15文件**；候选增加两个独立风险owner文件，增量来自真实执行与持久证明，不以总数为目标。产品代码只有新增migration；未创建生产测试接口。

**Peer HTTP 执行边界补齐：** 全仓没有其他keeper；共享 `semantic-retrieval-and-peer-capabilities.md` 明定只允许已证实未执行时换候选。实际调用者有Extension管理、rumination、semantic/lexical facade，副作用使不确定结果重放有实际风险。新增 `tests/peer/integration/test_http_delegation.py` 的一张双行参数表（R）：从真实PostgreSQL advertisement/lease经公开 `PeerManager.delegate`，调用真实localhost HTTP端点。唯一固定接缝是已有随机源的候选顺序；没有替换发现、factory、HTTP client或执行决策。

- 第一端点返回精确 `InkCre-Peer-Execution: not-executed` 时，第二端点收到请求并仅执行一次；独立验证方法、重复query、JSON body及标准PyJWT解码的auth合同。
- 第一端点完整收到body、记录实际测试副作用后关闭socket且不返回响应时，调用者收到 `PeerOutcomeUnknown`（cause为真实HTTP `RemoteProtocolError`），第二端点零请求，副作用仅发生一次。
- 两个进程内变异控制分别移除marker判定、把unknown分支改为继续候选；keeper各因目标决策出错红灯（0.44s / 0.37s）。没有改写生产源码，没有发现新的产品缺陷。
- 正常新表 **2 passed / 1.07s**；包括既有Peer数据库lease/冷启动失败的相邻suite复跑记录在 `peer-final.log`。Ruff、格式、Pyrefly **0 diagnostics** 和diff检查通过。已在原 `tests/peer/integration` CI目录，无需新路由。

**交接的剩余证明：** publication recovery测试源码名称耦合且只实跑拒绝脚本，saved artifact恢复仍交主线程release owner判断。Peer新证明不声称覆盖真实跨机丢包/超时/所有第三方代理；Loki fake HTTP证明编码和错误语义，不证明真实服务查询执行或外部认证。没有调用真实AI/Registry，也未在共享开发或预览数据库运行破坏性测试。
