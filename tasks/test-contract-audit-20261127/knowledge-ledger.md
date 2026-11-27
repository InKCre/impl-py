# 知识图、检索、整理与 MCP 审计

审计基点：`9f75cbb`。变更前覆盖 28 个测试声明；完整读取用例、参数表、fixtures、生产 owner、直接调用方和 CI 路由后形成。先完成只读账本，再按用户授权实施。不以测试数或删行数决定取舍。

## 当前结果

- 修复图导航 endpoint 分页遗漏的真实产品缺陷。三 Block + 10001 Relation 的 PostgreSQL 回归在原实现得到 `not_found`，修复后得到正确单跳路径。保留跳数与 Block 预算，不改 API。生产改动 `main.py` +21/-10；core fixed fragment 与 Unit TDD 已更新。
- 新增图导航 owner 3 个案例：真实分页/自环去重/endpoint closure/精确内容方向，正反向最短路/零跳/耗尽与预算区别，以及密集平行边回归。MCP TCP journey 增加一次真实 path/expand envelope 观察，算法证明仍归 manager。
- 收敛两例：producer graph oracle 已迁入 lexical producer journey；rumination context/方向/-11 局部 ID 已迁入真实 Agent loop。删除了直接调用私有 helper 的测试及其测试专用 `_invoke`。生产 helper 仍有真实消费者，保留。
- semantic 确定性 producer 验收启动并关闭真实 Job，3 个声明收敛为 2 个，只标 integration；真实 provider 单独标 acceptance。Organization human harness 补同样生命周期；multimodal live 明确 acceptance/integration。CI 入口由主任务整合。
- semantic provider/malformed-batch 负向核对目标实体与 `RuntimeError` / `ValueError`；把 fake 内部的请求断言移到返回后的观察，避免 caught AssertionError 假绿。MCP 负向分离 domain invalid_arguments 与 SDK schema validation，核对实际错误原因。
- 相关完整运行：`knowledge-all-after.log`，26 passed / 3 credentialed deselected，68.96 秒；Ruff 25 文件通过，focused Pyrefly 0 diagnostics（2 suppressed）。最后只修改分页测试的重复页防挂断言，单独重跑记录在 `knowledge-final-controls.log`。
- 收敛保留控制使用独立进程 monkeypatch 生产方法，未改共享 checkout：incoming→outgoing 在 context 断言失败；忽略 -11 起点在 GraphForm unresolved local ID 失败；HTML producer 换错 resolver 在迁移后的 graph oracle 失败。各日志为 `knowledge-mutation-{direction,local-id,producer}.log`，分别 2.50 / 1.43 / 3.61 秒，原因均是目标契约损坏。
- 交叉审查指出 rumination 收敛漏掉了“Agent 收到的 persisted ID 必须可继续消费”保护，已修复：真实 Agent loop 收集每次返回的 `id`，新 TestSession 按该 ID 读取并核对独立预期正文，focal→interpretation 的目标集合也使用返回 ID。进程内控制保留真实图写入，仅把返回 ID 换成不存在的正 ID，keeper 在 `returned_block is not None` 处失败（`knowledge-mutation-returned-id.log`，2.08 秒）。未恢复私有 helper 测试；修复后 keeper 通过，记录 `knowledge-returned-id-after.log`。
- 真实 AI、model quality 与 Human acceptance 未执行；自动化不能替代这些判定。Organization fixture 按相同生命周期修复，其真实 provider 路径未获运行证明。

以下逐项账本和层级计划是变更前的判断依据；上面的当前结果拥有完成状态。

## 结论与优先级

- **P1 / test-adequacy**：`GraphNavigationRetrievalManager.find_path` 和 neighborhood 没有维护中的执行测试；MCP journey 只在工具名单列出它们。REST `/graph-navigation`、MCP `find_path` / `expand_entities` 和 Organization Agent 都消费同一 owner。`_frontier_relations` 每一 endpoint 只取 10000 Relation，没有分页或截断信号；平行边能在很少 Block 内填满页面，可能把真实路径误报不存在。需实际 PostgreSQL 重现后修复，当前尚非运行证据。
- **P1 / test-adequacy**：semantic acceptance 的 3 个确定性、只需 DB 的 producer→持久化→检索案例未进入 `test:database`，且文件调用 `JobManager.run` 前不启动执行器。`Core.accepting_jobs=False`，`run` 直接返回 False；Organization credentialed acceptance 也缺生命周期 fixture。新隔离 DB 尚待主任务准备，需取得修复前实际失败。
- **P2 / test-hygiene**：semantic malformed-batch 只断言 `failed==2`、未入库；fake 中的 `assert len(inputs)==2` 也会被生产 `except Exception` 转成同样的失败结果。必须在 fake 外断言真实请求，检查诊断是基数校验 `ValueError`，而非 `AssertionError` / 无关 provider 故障。provider unavailable 对照也应核对 `RuntimeError` 诊断和目标实体。
- **P2 / test-hygiene**：MCP HTTP 的 invalid-input 参数组只查 `is_error`，未区分 Pydantic 输入拒绝、业务 `invalid_arguments` 和未预期内部错误。应保留负向，但绑定错误阶段/诊断；不要因是负向删除。
- **P2 / test-hygiene**：两组可安全收敛的重复建立流程：producer graph 单独案例可合入现有 lexical producer journey；rumination 私有 context/direct-tool 案例可把方向、负局部 ID 与持久化断言迁入真实 Agent loop 案例。先迁移全部保护再删除原例。
- **P2 / execution boundary**：multimodal live acceptance 未标 `acceptance` / `integration`；仅凭目录或当前缺少环境变量不能建立稳定离线门禁。其真实模型响应与固定素材识别独立于确定性 media integration，应保留并显式选择。

## 证据与 owner

- Graph：`inkcre_core/business/info_base/commands.py`（事务与局部 ID），`persistence/info_base/repository.py` / `uow.py`（实际查询、写入、flush），`business/graph_navigation_retrieval/main.py`（有向约束、最短路、截断语义）。消费入口：`app/routes/graph_navigation.py`、`app/business/sink/mcp.py`、`business/graph_navigation_retrieval/tools.py`。
- Semantic：`business/semantic_retrieval/main.py` 的 `_maintain.flush` 捕获所有 `Exception` 并把类名写入诊断；`_embed_and_upsert` 使用 strict zip 后才进入短写事务。`tests/semantic_retrieval/integration` 的失败断言没有消费该现成诊断。
- MCP：`app/business/sink/mcp.py` 的 query branch、mode result merging、PAT 与 lifespan；HTTP journey 是真实 TCP / 独立 MCP Client / 实际存储索引，discovery 是只读角色+SQL 白名单。两个层保护不同风险，不能互相替代。
- Organization：`business/organization/{synthesis,rumination,supersession,evidence_stance}.py`。synthesis basis 方向为 source→synthesis；故障注入测试读 source outgoing 正确，不是反向假绿。
- History：`9f75cbb` 新增 bounded metadata + HTTP journey；`9bcec9c` 补 MCP candidate labels；`c1e882b` 增 Markdown；`4fa74bb` 独立 SDK 后 Core 初始不接收 Jobs；`5b8dbf8` 异步事务迁移；`915be5a` Organization behaviors；`60d9e61` 图导航。旧历史实现不是删除依据。
- CI：默认 `testpaths=[tests]`；`repository_check.sh database-tests` 显式含 integration 子树，但不包含 semantic acceptance 确定性案例。真实 provider/人工判定必须另行验收，绿色自动化不证明模型质量。

## 声明级账本

R=保留；F=保留契约、修复证明；C=先迁移至指定 keeper 再收敛；D=确认无契约后删除。本 lane 无无条件 D。

| 文件 / 测试声明 | 标记 | 独立契约、可信回归与剩余证明 |
| --- | --- | --- |
| `tests/info_base/test_markdown.py::test_markdown_keeps_source_and_unicode` | R | Markdown 不损坏源文、代码缩进、链接与 UTF-16 bytes；真正 MarkdownResolver 解码，mock 仅替代 hydration I/O。local-files 读取不是这个 SDK resolver 契约的完整替代。 |
| `tests/test_mcp_recall.py::test_query_labels_preserve_evidence_and_unreadable_candidates` | R | 两模式 evidence 合并、lexical label 优先、unknown resolver 候选不丢弃、Relation 可读引用。HTTP keeper 只逐模式排名，不能替代这个合并保护；fake 仅提供领域检索结果，真正 MCP assembly 被测。 |
| `tests/info_base/integration/test_graph_commands.py::test_submit_graph_creates_arbitrary_links_and_returns_only_block_mapping` | R | Flat graph 两向关系、局部 ID 映射与响应只含 Blocks；新 TestSession 独立回读真实 rows。映射值来自返回是动态 DB identity，不是复制被测决策；关系期望独立指定。 |
| `...::test_repeating_negative_ids_requests_new_blocks_each_time` | R | Flat negative IDs 每次提交新建，与 Stars reconcile 的不同身份合同；防错误跨调用 dedup。可留为小案例，不能和 Stars 等同。 |
| `...::test_recursive_stars_authoring_retains_reconciliation_and_direction` | R | Stars 相同身份协调、incoming/outgoing 方向、真实三节点双边；Flat case 不拥有 resolver dedup。 |
| `...::test_composed_graph_failure_rolls_back_earlier_graph` | R | 两图同一 UoW 原子回滚；真实 FK violation 类型已核对，目标缺失先查；新连接读取未残留。不是泛异常负向。 |
| `...::test_cancelled_graph_operation_rolls_back_and_releases_connection` | R | CancelledError 回滚后后续操作成功，并新连接证明只有一次提交。正常 FK 失败不能证明取消清理。 |
| `...::test_blob_graph_transaction_and_hydration` | R | PostgreSQL bytes 真写→BlockService 重读→hydration；另一事务中 bytes 写入随异常回滚。保留 storage/graph 共同原子性，不用 action return 替代存储。 |
| `...::test_github_stars_retain_node_identity_and_reject_ambiguity` | R | GitHub node_id 独立于 login，重复 persisted identity 明确拒绝；实际 resolver + graph UoW，异常匹配 `multiple Blocks`。与普通 Stars exact-content 是独立 extension identity。 |
| `...::test_async_entity_routes_preserve_crud_and_hydrated_projection` | R | HTTP body/状态/字段与真实 CRUD、hydrated readback；domain transaction case 不覆盖 transport 输入/输出，保留。 |
| `tests/info_base/integration/test_mcp_discovery.py::test_metadata_and_condition_pages_never_load_secrets_or_handlers` | F | 保留只读 role、SQL 列白名单、DB 与本地 registry 分离、metadata paging、scope cursor；SQL inspection 有独立读取边界意义，非源码 grep。末尾篡改 cursor 组需同其他组核对 invalid_arguments 诊断，防 generic error 假绿。 |
| `tests/info_base/integration/test_mcp_http_journey.py::test_mounted_http_metadata_query_read_and_prefiltered_ranking` | F | 保留真实 TCP / PAT / enable-disable-reenable / shutdown、两类索引 prefilter-before-topK、独立 transport SDK JSON；补 invalid-input 错误原因。工具清单仅是公开八工具合同，不能当 find_path 真实行为覆盖。 |
| `tests/lexical_retrieval/integration/test_lexical_retrieval.py::test_literal_chinese_ranking_freshness_and_cascade` | R | 独立 phrase-vs-terms 顺序、中文 substring、编辑失效与维护修复、Block 删除 cascade；真实索引不由 fixture 预置。 |
| `...::test_unknown_resolver_is_bounded_unavailable_diagnostic` | R | unknown exact resolver 产生 bounded unavailable 而非 failed/empty；核对 block + reason，负向到达正确分支。 |
| `...::test_direct_and_cron_created_jobs_share_exact_handler_path` | R | 手动/cron 创建后执行真实维护，重新读取 Job FINISHED + indexed；不是只断言入队。但结束未清理 cron/job，适用于 disposable、串行 suite；不声称共享 DB 隔离。 |
| `tests/semantic_retrieval/integration/test_semantic_retrieval.py::test_real_maintenance_freshness_and_global_retrieval` | F | 保留跨 Block/Relation ranking、实体过滤、维度/endpoint/profile 失效、恢复、provider/malformed batch 原子性；补错误原因与 fake 外请求观察。2ms sleep 不该被视为产品同步保证，可用数据库时间因果证据取代。 |
| `tests/organization/integration/test_behavior_graph.py::test_exact_behaviors_compose_into_replayable_graph_use` | R | 六种独立组织效应的真实 persisted replay/reconcile；supersession cycle 和 opposite stance 已匹配原因，Duplicate components 真实穿透图。部分行为只查返回 receipt；应补真实消费者 lineage/source basis 阅读，可优先在现有用例内补而非复制新层。 |
| `...::test_connected_component_reports_missing_and_bounded_incomplete_proof` | R | 缺失 seed 与 exploration incomplete，不能将截断误报不连通。真实 DB 图且限制达到，和完整 components 正向独立。 |
| `...::test_synthesis_rolls_back_block_and_basis_on_write_failure` | R | 第二条真实 Relation flush 后故障，重新 UoW 证明 synthesis 和 source outgoing 全回滚、source 仍在。生产 seam 既有 repository method；无需删 fault injection。 |
| `tests/organization/integration/test_rumination_graph.py::test_context_preserves_direction_and_draft_submit_maps_local_ids` | C | direction/context 和 -11 signed mapping 必须保留，但当前调用私有 `_build_initial_message` / `_bind_tools`。把邻居、独立 context oracle、-11 mapping 与持久化 readback 移入下一个真实 Agent loop keeper 后删除本例。 |
| `...::test_explicit_rumination_runs_real_agent_tools_and_repeats_additively` | F | 作为 rumination keeper，真实 Agent executes draft→submit→second additive run，DB 重新回读两次图。吸收上例 direction/context/-11，保留模型只为外部 I/O；不把 deterministic chat 算语义质量。 |
| `tests/organization/integration/test_media_interpretation.py::test_media_textualization_interpretation_and_lexical_recall` | R | 真实 bytes storage、PDF/subtitle解析、faithful/interpretation authority 分离、exact Job state、缺能力诊断、重复不额外模型调用、检索回读。固定模型文本只是 I/O fixture；真实模型识别另有 live keeper。 |
| `tests/semantic_retrieval/acceptance/test_vertical_quality.py::test_real_producers_create_the_authoritative_corpus_graph` | C | 所有 `_assert_ingested_graph` 断言迁入下一 lexical producer keeper；两者使用同一 `_ingest_corpus`，单独再次完整生产不增加 transport/storage 风险。先迁移后删，不能直接把 graph asserts 丢掉。 |
| `...::test_real_producer_corpus_supports_exact_and_chinese_lexical_recall` | F | keeper：真实 Memos HTTP、RSS/Atom HTTP、HTML byte storage→真实 graph→lexical，独立 corpus phrase/中文判定。补 Job lifecycle，吸收 graph oracle，纳入 DB gate。domain lexical test 用 raw rows，不等同 producer mapping。 |
| `...::test_vertical_quality_control_flow_with_deterministic_ai` | F | 独立整个 producer→Agent rumination→存储→semantic ranking wiring 与可执行 corpus harness；补 Job lifecycle、进入 DB gate。固定向量按 corpus 注入，明确只证明 control flow，不声称 embedding/整理质量。 |
| `...::test_real_provider_quality_and_rumination_gain` | F | 保留真实 provider 的独立质量与工具协议风险；补 Job lifecycle，仅显式运行。oracle 为 manifest top3 + distractor/原文 outrank，并确认 judged embeddings fresh；不由模型自产期望。当前未运行。 |
| `tests/lexical_retrieval/acceptance/test_multimodal_live.py::test_real_multimodal_materialization_interpretation_and_recall` | F | 保留真实模型 OCR/STT/视频媒体、真实存储和后续 lexical 读取；补 acceptance/integration 标记。固定输出指令只验证真实 tool-call 数据链路，不能当 interpretation 质量；素材内容独立识别是另一有效 oracle。当前未运行。 |
| `tests/organization/acceptance/test_black_box.py::test_two_information_worlds_are_organized_for_human_review` | F | 保留 Human review harness：两世界真实模型 Jobs、上游变化、graph 与 later-use evidence；不预置行为结果符合 best effort。补 Job lifecycle；必须在交付表述明确自动通过只证明执行、人工质量待审，当前未运行。 |

## 分层切换计划

1. **真实缺陷与主路径**：新增一个 manager PostgreSQL 图导航 owner，使用写入真实图检查 direction/content/分页/self-loop closure/shortest/bounds；针对 10000 Relation 截断分别记录修复前失败与修复后通过，不复制到 REST/MCP 各层。现有 HTTP journey 可加一次真实 `find_path` / `expand_entities` 调用只证明 envelope。
2. **候选收敛**：先把 producer graph oracle 搬入 lexical producer keeper，再删单独 graph case；先把 rumination context/ID oracle 搬入真实 Agent loop，再删私有 helper case。无生产 seam 因此变成死代码：`_build_initial_message`、`_bind_tools` 都有真实调用，保留。
3. **hygiene**：negative assertions 使用现有 domain diagnostic/error contract，不新加生产测试导出；semantic 断言 fake 请求与 ValueError 原因；MCP 区分 SDK validation 与业务 invalid_arguments。
4. **execution boundary**：父任务统一 CI/pytest route；semantic 确定性案例与 credentialed case 分类，acceptance 标记和显式命令一致。所有 DB-only keeper 跑一次串行数据库 lane；real provider/人工质量不能用 skip 记成功。
5. **保留与反例验证**：收敛后分别有意破坏 incoming direction、signed local ID、RSS semantic child、semantic invalid cardinality 路径，验证 keeper 变红，逐次恢复生产源文件。类型/lint 和相关 owner/sibling 绿色后停止扩大验证。

## 基线运行证据与限制

根任务的离线基线：224 passed / 85 skipped；本 lane 无数据库时只有 Markdown 与 MCP recall 执行，其余明确 skip。随后根任务 DB 基线记录三个 deterministic semantic 案例因未启动 Jobs 而在 `assert JobManager.run(...)` 失败；本 lane 路径缺陷的独立前后证据为 `knowledge-path-before.log`（1 failed / 1.65 秒）与 `knowledge-path-after.log`（1 passed / 1.63 秒）。限制见当前结果。
