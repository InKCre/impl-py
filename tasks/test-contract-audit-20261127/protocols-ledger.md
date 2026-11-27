# CalDAV／Twitter 协议测试审计

审计基点：`9f75cbb`。本账本来自完整测试正文、参数表、fixture、生产 owner、文档、调用方和引入历史；先完成只读账本与层级方案，再按总任务授权实施。下方逐声明标记记录基线判断，末节记录已完成结果。`R` 保留，`F` 保留契约但修复证明，`C` 将有价值部分并入已命名 owner 后删除原测试，`D` 无契约可删。未发现足以直接标记整项 `D` 的证据。

## 基线判断

- **test-hygiene：FAILED（P2）**。主要问题是少数断言锁定实现、个别测试名比实际证明更强，以及负向／提交故障证明不够精确。大多数测试保护独立契约，不能因静态、负向、mock 或耗时而删除。
- **test-adequacy：FAILED（P1）**。这些协议契约没有持续执行入口。`pyproject.toml` 只收集 `tests/`，`repository_check.sh database-tests` 未包含本 lane；CI 唯一显式 `scripts/tests` 入口只选文档与 preview 两文件。这是历史明确决策，不是意外漏配：`docs/30-unit-tdd/caldav-extension.md` 的「验证依据」、`twitter-extension.md` 末段明确称其为候选／排查脚本。完整审计可将成熟且有独立风险的 owner 接入门禁，并同步文档，不应只增加无人执行的测试。
- 未确认产品缺陷。发现的误导性证明应先修复，再对改进后的 keeper 进行针对性变异验证；不把证明不足写成产品错误。

## 基线和归属

复用本任务 `baseline.xml`，命令为 `pdm run python -m pytest -q -rs tests scripts/tests deploy/integrations/chatgpt/tests ...`。本 lane：77 passed、21 skipped；跳过均缺显式 PostgreSQL（CalDAV 11、Twitter graph 9、HTTP roundtrip 1）。这些跳过不算行为通过。

| 文件 | 基线 | 主契约／生产 owner |
| --- | --- | --- |
| `scripts/tests/caldav/test_codec.py` | 3 passed | 独立图语法、原生参数和二进制编解码；`codec.py` |
| `scripts/tests/caldav/test_dav_graph.py` | 6 skipped | HTTP/WSGI、条件写、事务提交、权限撤销、锁；`protocol.py`、`storage.py`、`graph.py` |
| `scripts/tests/caldav/test_semantic_graph.py` | 5 skipped | 共享图引用、字节权威、跨 Peer 修改、旧数据升级；`graph.py`、`resolver.py` |
| `scripts/tests/twitter/test_archive_contract.py` | 51 passed | 固定 x-monkey DTO、共同模型转换、稀疏合并；`archive_schema.py`、`x_monkey.py`、`archive_merge.py` |
| `scripts/tests/twitter/test_archive_http.py` | 16 passed | Cookie、Origin、JWT 与路由发布；`archive_http.py`、`archive_session.py`、Extension |
| `scripts/tests/twitter/integration/test_archive_graph.py` | 9 skipped | 真事务、身份、引用、延迟媒体、索引、provider adapter；`reconcile.py`、`api.py`、`bookmark.py` |
| `scripts/tests/twitter/integration/test_archive_http_roundtrip.py` | 1 skipped | 发布的 HTTP → PostgreSQL → 新查询请求，含预存作者身份保护 |
| `tests/extensions/twitter/test_bookmark_backfill.py` | 7 passed | 有界分页、请求预算、顺序异常；`Source.backfill` |

历史：`fbe07f5` 引入 CalDAV 图与上述脚本；`10ac178` 引入 x-monkey、共同归档与脚本；`c30319e` 引入有界 backfill。`4fa74bb` Core 分离补入实例绑定 fixture，说明执行上下文是独立生命周期风险。CalDAV JSON fixture 与 impl-ts 对应 fixture 内容语义一致（文本 diff 仅排版）；TS `graph-content.test.ts` 消费同一 graph grammar。Twitter consumer 合同见共享 `twitter-archive-contract.md`、本地 `twitter-extension.md` 和实际 x-monkey 路由；Twikit 用例使用安装依赖的真实 `Tweet` getters，已核对 `full_text` 的 note-tweet 分支及 `in_reply_to`、时间 getter。

## 逐声明账本

以下表中省略共同 `test_` 前缀；同一参数表有相同判断时一行覆盖所有参数，差异在证据中说明。

### CalDAV codec

| 声明 | 判断 | 契约、可捕获回归和证据 |
| --- | --- | --- |
| `native_graph_roundtrip_preserves_components_parameters_and_bytes` | R | 2 个独立 authored fixture 的 native text、remainder、属性、照片／提醒字节；精确文本来自 fixture，不由被测输出生成。还以明确诊断拒绝双重权威。Python/TS 独立实现需要此层，不被 DB 往返替代。 |
| `icalendar_binary_and_uri_do_not_accumulate_encoding` | R | 重复编解码仍保留固定二进制和 URI `,;`；捕获 vobject 重转义累积，独立固定字节 oracle；不是空洞 roundtrip。 |

### CalDAV HTTP 和事务

| 声明 | 判断 | 契约、可捕获回归和证据 |
| --- | --- | --- |
| `native_roundtrip_graph_conflicts_and_sync_recovery` | R | 重复、例外、提醒、未知字段、resolver 可读；Barrier 并发同 ETag 只一成功；同步 token 过期后 403 + valid-sync-token 并可重新枚举。真实写后新 GET，保留。 |
| `commit_failure_rolls_back_and_never_reports_success` | F | 原子提交是独立契约，`after_flush` 标记后在 `before_commit` 抛错合理；但只断言 500，不能排除在实际 flush 前的其他 500。加故障触发记录，确认注入命中；保留新 GET 的正文/ETag 回滚证明。 |
| `tasks_contacts_and_credential_revocation` | R | VTODO 与 vCard 独立 native 字段，真实修改 Extension 持久 config 后凭据即时失效，unpublish 后 404；保留，不与 codec 合并。仅未覆盖再次发布后恢复。 |
| `move_rejects_stale_source_and_retains_graph_identity` | F | 已证明旧 ETag 412、新 ETag 201、旧路径 404、新路径内容；**未证明名字中的 graph identity**。MOVE 若 delete+create 将仍通过。记录写入后的 root ID，MOVE 后从新路径读取并断言同 ID，最好保留指向根的外部 Relation。 |
| `collection_delete_serializes_direct_peer_membership_writes` | R | move/insert 两行分别保护已有关系修改和 FK 新插入的锁；Event 控制目标事务进入、SQLSTATE 55P03 排除其他错误。直接 Peer 写是 DAV advisory lock 外的独立风险，不可用并发 PUT 代替。 |

### CalDAV 共享信息与升级

| 声明 | 判断 | 契约、可捕获回归和证据 |
| --- | --- | --- |
| `reusable_properties_reorder_shared_edits_and_publication_delete` | R | 仅改标签/顺序保持内容 ID；其他 Peer 改电话/blob 改变 GET/ETag；源换值不重写他人引用；撤下发布后原根/引用/照片仍能读取。真实落库与跨事务读取，保护图 authority 而非内部属性清单。 |
| `child_before_parent_becomes_a_real_relation_and_survives_unpublish` | F | 父未出现时文本引用，出现后指向父 ID，具独立契约；删除父后仅断言 UID 字符串，无法区分保留真实父引用与退化为 UID 文本。删除后再次读图，断言 target 仍是 parent.id 且父根存在。 |
| `conditional_write_locks_the_actual_semantic_dependency` | R | phone/blob 两行是不同持久 owner 的直接 Peer 写锁，Event + SQLSTATE 55P03 精确；根更新或 advisory lock 测试不能替代。 |
| `legacy_promotion_is_atomic_keeps_root_id_and_unknown_fields` | R | 真创建 v1 记录和外部引用，真实 `_create_value` 完成后定点故障、事务回滚、恢复迁移并读回；保留注入接缝，根 ID 和外部引用 oracle 独立。 |

### Twitter 固定协议与稀疏合并

| 声明 | 判断 | 契约、可捕获回归和证据 |
| --- | --- | --- |
| `native_batch_preserves_complete_source_and_field_presence` | R | 完整独立 fixture 在 DTO 中不丢未知允许字段、Unicode 和 absent/present；不是只断言 schema 的声明列表。 |
| `missing_and_explicit_empty_batches_remain_distinct` | R | 固定协议 `{}` 与 `{posts:[]}` 序列化不同，Pydantic default factory / exclude_unset 回归会失败。 |
| `fixed_protocol_roundtrips_through_owned_facts_and_neutral_results` | R | DTO ↔ native Adapter、found/missing/failed、accepted/unchanged 的转换；mock 只提供独立中性存储结果，真实 adapter 决策，保留。 |
| `capture_batch_does_not_inherit_remote_request_limits` | F | native capture 无 1000 限额而 wire 有限额的独立 boundary。保留，但 wire 负向需确认 `posts` 的 too_long 错误，不能任意 ValidationError。 |
| `fixed_export_projects_additive_native_fields_and_retains_open_json` | F | native 未来新增字段递归剔除而 open Article JSON 保留；真实生产 projection。最后严格 DTO 拒绝需断言 extra_forbidden 的字段位置。 |
| `ids_are_positive_decimal_strings` | F | 9 行分别覆盖零、前导零、负数、小数、空白、换行、非 ASCII、数值、布尔；全部有真实跨语言字符串 ID contract。加 loc=`posts,0` 和 error type，排除无关字段错误。 |
| `invalid_native_records_fail_instead_of_coercing_or_discarding` | F | 28 行各只改完整 fixture 中一处，输入可到对应 guard；覆盖 UTC 格式/日期、严格 count、finite、范围、非空列表、null、unknown fields。原表值得保留；先确认原 fixture 有效，并断言错误 loc 是修改路径（某些复合类型 error loc 包含 union 后缀），避免将来 fixture 其他错误导致整表伪绿。 |
| `query_and_submission_have_per_kind_limits` | F | users/posts/media 各自 1000 接受 1001 拒绝，保留协议预算；分别确认对应列表的 too_long 错误。与 capture 无上限是独立 boundary，不合并删除。 |
| `query_submit_and_session_wire_roundtrip` | R | 固定响应结构的 absent 与 null、status 和汇总 wire contract；fixture 与实现独立。最后 createdAt null rejection 可并入上方 invalid 表保持归属，但无需整项删除。 |
| `merge_is_recursive_replaces_arrays_and_does_not_mutate_inputs` | R | 深层合并、数组替换、调用方后续修改不能污染输入，固定 literal 预期；独立 pure invariant 比数据库边界更精确且廉价。 |
| `counter_observations_merge_independently_and_compare_instants` | R | 逐计数新旧时间比较、同时间覆盖、无时间新增；固定预期，跨语言语义。 |
| `untimed_changes_remove_only_the_changed_counters_previous_time` | R | 无时间新值只移除变化计数时间，全部清空时不保留空 map；与 DB 中单计数步骤有独立多计数风险。 |
| `equal_counts_keep_new_observation_metadata_and_replay_is_unchanged` | R | 值不变但新时间也是更新，重放幂等，孤立时间不能创造计数；保留。 |
| `capture_time_comparison_matches_javascript_millisecond_precision` | R | JS 毫秒对齐下亚毫秒不同应视为相同时刻、后写覆盖，独立跨语言 contract；不能因纯函数测试删除。 |

### Twitter HTTP/session

| 声明 | 判断 | 契约、可捕获回归和证据 |
| --- | --- | --- |
| `session_lifetime_defaults_to_seven_days_and_supports_environment_override` | R | 7 天是公开配置默认，环境覆盖真实 Settings 解析；独立配置 contract。 |
| `connect_exchanges_same_origin_bearer_for_scoped_cookie` | R | JSON/Bearer → cookie flags/path（含 public prefix）→ 后续请求读到真实会话；测试固定 route 前缀虽模拟代理，契约值独立。 |
| `session_exchange_rejects_non_peer_or_cross_origin_credentials` | F | 缺 Bearer、session 当 Bearer、跨 Origin 3 个独立 guard。已有状态足以区分多数前置错误，但 session_as_bearer 可因 helper/token 错误伪过；确认 token 对普通 session 有效并断言专有诊断。 |
| `account_page_uses_nonce_csp_and_only_same_origin_webcrypto` | F | nonce/CSP/no third-party-script 是有效静态外部合同；`status.textContent`、变量名 `crypto.subtle`、fetch 源字符串、HTML 序列化确切片段锁定实现且不证明实际连接。保留响应头/nonce/无外部脚本与密码字段的语义解析，删除脆弱 JS 字符串断言；真正“只在 cookie readback 后 Connected”需浏览器执行，不可声称已由此测试覆盖。 |
| `account_page_shows_peer_and_expiry_for_an_existing_session` | F | 页面显示 peer/到期有效；但 expected 调用同一个生产 `_format_expiry`，格式错误会双方同错。独立按 UTC RFC3339 计算期望，避免私有 decoder 作为唯一期望来源。 |
| `session_get_needs_no_json_or_client_header_and_accepts_extension_origin` | R | GET 无 client header 仍可检测，extension Origin 合法、account URL 前缀、无提前续签；独立于 POST guard。 |
| `session_renews_cookie_inside_half_life` | R | 真实 HTTP request、独立 jwt claims 修订、时钟注入、到期变为 now+lifetime；保留确定性时钟。可用独立 jwt.decode 验证新 token，减少私有解码依赖。 |
| `session_rejects_null_insecure_or_unknown_origin` | R | 4 行使真实 Origin filter 拒绝；GET 本来允许无凭证返回 200，所以不会因缺 cookie 假通过。 |
| `disconnect_is_limited_to_core_origin_and_clears_the_cookie` | R | 同 Origin navigation 才清 cookie 并跳回精确前缀；foreign 请求 403；有独立 CSRF／Cookie scope 契约。 |
| `query_requires_client_headers_and_session_cookie` | R | 真实 HTTP header/session guard，业务 query 可用 fake 避免数据库；缺 cookie=401、缺 client=400，正控制到达 query。调用 shape 不是唯一断言。 |
| `submit_passes_validated_batch_to_native_protocol` | C | 业务被替换为直接产出断言值的 fake，仅证明 wiring。将 `{posts:[]}` 缺省区别和 summary=0 的实际 HTTP 断言先迁入 `test_real_http_submit_query_preserves_native_wire_payload`，再删除本声明。keeper 同时保留真实完整 payload，仍是 1 个 HTTP→DB owner。 |
| `session_does_not_renew_for_no_cors_subresource` | F | 403 防无 CORS 状态请求有效；使用新 token，原本就不会续签，因此“无续签”未有效验证。改为半衰期内有效 token，配一个同 token CORS 正控制可证明拒绝发生在续签前。 |
| `extension_publishes_claimed_archive_routes` | R | 实际 Extension publication 让 auth-neutral archive 可达，而 setup 保留 Peer auth；不只是 route list，跨 Host composition 独立风险。 |

### Twitter 真实图与 provider

| 声明 | 判断 | 契约、可捕获回归和证据 |
| --- | --- | --- |
| `native_roundtrip_projection_and_media_are_lazy` | R | 真写→query→resolver→lexical maintain/retrieve；无媒体 byte fetch、无 blob/job/content，重放同根与 quote placeholder。无资源负向是明确防自动下载/付费契约，不能删。 |
| `sparse_counter_history_and_managed_edges` | R | 真提交后新 Session 检查稀疏保留、counter 状态、root ID；数组引用变化与用户 citation 不变。与 pure merge 独立事务/managed edge 风险。 |
| `bookmark_joins_legacy_and_native_identity_without_overwriting_text` | R | 真实 v1 ID 和外部引用保留，Bookmark sparse supplement 不覆盖 native 正文且 cursor 真落库；只 mock provider，不 mock 合并。 |
| `real_transaction_lock_preserves_concurrent_supplements_and_rollback` | R | Event 控制事务重读锁，最终两字段同根；实际 Relation 写阶段故障，query 缺两个记录、随后重试成功。合理故障接缝，保留。 |
| `batch_lookup_tolerates_non_json_and_reports_locatable_corruption` | R | 普通非 JSON、损坏 JSON 与能定位的损坏记录三个区别，真实查询 `found/failed`；防数据库 JSON cast 一条毒化整批。 |
| `media_metadata_roundtrips_without_content_blocks` | R | 4 种媒体 URL/HLS/variant 元数据均按原样储存，避免在 metadata 阶段过度网络过滤或创建下载内容；独立于完整 fixture 的普通合法媒体。 |
| `representative_batch_keeps_database_round_trips_bounded` | R | 600 条正常批次在真实 DB 完成，SQL round-trip <20 对 remote DB 持锁延迟具明确解释；不是 arbitrary helper call shape。保留，不按慢标签删。 |
| `official_provider_keeps_source_text_and_exact_id_semantics` | R | Official 原文解 HTML、会话 ID 与 reply ID 区别，真实 adapter 然后落库；已有 Twikit 测试不能替代 provider 字段差异。 |
| `twikit_bookmark_first_then_native_enrichment_keeps_one_root` | R | 安装依赖真实 Tweet getters，长文、emoji、media、reply，Bookmark→索引不下载媒体，随后 native enrich 同根。公开 provider 结构和持久身份风险独立。 |
| `real_http_submit_query_preserves_native_wire_payload` | R | 已发布路由，session headers，真写后新 Session／新查询请求，预存作者复用而 cleanup 不删他人作者；这是接收被合并 submit wiring proof 的 keeper。 |

### Twitter backfill

| 声明 | 判断 | 契约、可捕获回归和证据 |
| --- | --- | --- |
| `backfill_range_crosses_pages_and_stops_at_limit` | R | skip/limit 跨页、last request 缩小预算、3 次物理 provider request、marker 不写；fake persistence 只观察 selection，测试不应宣称真实落库。 |
| `backfill_reports_exhaustion_before_requested_range` | R | provider 提前结束使 saved=0/exhausted true 且只 1 次请求；独立 range-before-start 情况。 |
| `backfill_treats_empty_cursor_as_exhausted` | R | 实际 Twikit 返回空字符串 cursor，避免继续请求／误报未结束；独立正常终止 contract。 |
| `backfill_does_not_report_uncommitted_page_as_saved` | F | 仅在 persist mock 内抛错，且 fake 把结果直接 append 内存；**未经过 UoW 提交失败**。保留 failure-before-write 行，并在真实 DB graph keeper 覆盖第二页 after-flush / before-commit 故障、首段保留、第二段缺失、marker 不变和 Job state 未前移；或者将原测试改名为准确 selection failure，不把它计为事务 proof。 |
| `backfill_stops_on_ambiguous_provider_continuation` | R | repeated cursor、重复帖子、空页带 cursor 各有准确错误消息；先一页成功保证进入继续分页，不被前置失败混淆，保留。 |

## 第二遍：层级方案与删除后的证明

1. **协议 owner 持续执行**：离线 codec、wire 与 session 进默认 hermetic 入口；DB 的 CalDAV 图/HTTP 与 Twitter 图/HTTP 进显式 disposable DB lane。排除真 provider 凭证需求与 phone 验收，不用一份脚本把所有外部环境模糊混合。命令和 CI 改由总任务统一修改，防止多人改 shared harness。若移动文件，则 fixture path/import/文档一并修复；仅路由现有目录也可，目录名不决定测试价值。
2. **唯一整项合并**：空 submit HTTP 验证迁入真实 HTTP keeper 后删除 `test_submit_passes_validated_batch_to_native_protocol`；没有生产导出只为该声明存在，因此不删生产 seam。其余 52 声明保留或修复；不存在证据支持批量退役整个 codec/schema/mock 层。
3. **优先修复结果证明**：MOVE root ID、撤下父对象仍引用真实 parent、CalDAV commit 注入命中；backfill 真实 commit failure 的独立 PostgreSQL 增量；session no-cors 使用可续签 token；expiry oracle 独立。
4. **负向表**：扩展已有参数表以保存 expected loc/type；先有效 fixture 正控制，保留严格字段契约。不要复制 schema 成另一份 manifest，也不要为每个 field 新建单测。
5. **状态恢复缺口**：CalDAV 当前有事务 recovery，但没有同一对象经扩展停止→重新发布→新 client 再读的自动 proof。可在现有 DAV journey 重开 Extension runtime 并读回根 ID、ETag、内容来关闭，不新增生产 reset 方法。已有真机验收是历史证据，不证明当前 head。
6. **账户页 JS**：HTTP suite 可以保留 header/DOM 语义契约，变量名和 fetch 源码 grep 不算浏览器行为。独立实际浏览器连接验证尚未执行，不能通过删这些断言宣称交互已覆盖。

## 验证建议与边界

- 离线：`pdm run python -m pytest -q scripts/tests/caldav/test_codec.py scripts/tests/twitter/test_archive_contract.py scripts/tests/twitter/test_archive_http.py tests/extensions/twitter/test_bookmark_backfill.py`。
- 数据库：显式本任务一次性 `INKCRE_TEST_DATABASE_URL` 后运行整个 CalDAV/Twitter 目录；重复 setup/disposal 可能暴露不同 asyncio loop 的池问题，不能用共享/preview 数据库绕过。
- 证明保留：针对 MOVE 重建根、删除 parent reference、成功前发送 HTTP 200、将 backfill state 更新移入提交前、允许 no-cors 续签做单点控制／变异，确认 keeper 因目标契约失败。恢复源码后重跑受影响 owner。纯整理名称/断言不要求整个产品全量重跑。
- 限制：本审计无现场真实 X、x-monkey userscript、DAVx⁵ 手机/第三方 HTTPS proxy 验收；固定 provider 输入只证明当前 adapter 对已知响应，不能证明实时第三方 API、OAuth 或计费。CalDAV 写事务读取全图和逐依赖锁是既有设计成本，此审计未变更架构。

## 已完成变更与证据

实现上述 F/C 项中的直接改进，未永久修改产品实现或增加生产测试接口。新增两项 keeper：

| 声明 | 判断 | 独立价值 |
| --- | --- | --- |
| `test_restart_restores_published_graph_without_recreating_objects` | R | 实际撤销路由、释放旧运行态，再创建新的 app/client，按相同路径读取原 root ID、正文和 ETag，防 runtime 缓存代替持久化。 |
| `test_backfill_preserves_committed_page_when_next_commit_fails` | R | 两页均经过真实 source UoW；第二页 flush 后在 commit 注入故障，新 query 确认首段 found/第二段 missing、source marker 不变、job progress 停留首段。旧离线测试改名说明它只证明 persistence call 失败时不前移。 |

修复了完整数据库基线暴露的跨测试污染：`test_tasks_contacts_and_credential_revocation` 将 `hello` 作为 PNG blob 写入，撤下发布后图仍保留；后续 Twitter 全库 lexical maintain 在 block 175 报 `ResolverContentError`。对该 ID 的原始 SQL 读回确认关联 CalDAV contact，blob 长度为 5、hex 为 `68656c6c6f`。fixture 改为真实 PNG，并在本用例完成保留断言后清理通过 SQLAlchemy after_flush 记录的精确 Block/blob ID；不按全库差集清理，不改 lexical failed==0 断言。这是测试生命周期缺陷，不是产品应把 DAV 删除变为擦除。

唯一整声明合并：删除 mock-only `test_submit_passes_validated_batch_to_native_protocol`，其空提交、extension Origin 和独立零 summary 响应被真实 HTTP→PostgreSQL journey 接收。createdAt null 负向从响应 roundtrip 移入现有 invalid 参数表。账户页保留 CSP/nonce/密码字段的响应语义，去掉变量名、fetch 源字符串和 HTML 序列化片段断言；不声称此检查证明 JS 点击流程。

最终执行：独立 `audit_protocols` 数据库上运行整个 CalDAV/Twitter 目录及 backfill 文件，**100 passed，22.66s**，见 `protocols-final.log/xml`。此前同 scope 第一轮 **100 passed，35.60s**。最终 Ruff check / format、git diff --check 通过；定向 Pyrefly **0 diagnostics (1 suppressed)**，沿用既有 suppress。两个警告为 Starlette/AnyIO、Radicale/importlib 的依赖弃用信息。

5 个单点生产变异均被目标断言捕获，见 `protocol-control-*.log`：MOVE 重建根在 ID 断言失败；撤下父发布后退化 UID 文本在目标 ID 断言失败；backfill 提交前更新进度在 job state 断言失败；错误日期格式在独立 RFC3339 literal 断言失败；移除 no-cors guard 在 403 断言失败。`protocol-controls.py` 每次通过 finally 逐字恢复源文件，最终生产目录无本 lane 差异。变异用于证明新 keeper 能区分可信错误，不是已发现的产品缺陷。

**test-hygiene：PASS（本 lane 已实施变更）**。独立契约和合理故障接缝保留；**test-adequacy：本 lane 定向通过，CI 持续路由与跨 lane 汇总由总任务负责**。本 lane 最终数据库运行在新增 `c7f2a6d9e410` 迁移落地前完成；该迁移由总任务的整合全量验证承担。第三方实时协议、手机及浏览器点击的限制仍如上，不以 mock／固定输入代替。
