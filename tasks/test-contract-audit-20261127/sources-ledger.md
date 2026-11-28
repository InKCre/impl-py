# 来源与读取分发审计账本

审计基点 `9f75cbb`，范围为 `tests/extensions/**`（不含 Twitter）及其生产 owner。完整读取 9 个测试文件、33 个测试声明及全部参数行，共 2,666 行；支持代码另有 `runtime_support.py` 105 行、`imap_harness.py` 237 行。初始无数据库基线为 50 passed / 23 skipped，来自根任务 `baseline.xml`；跳过不作为通过。读取了相应生产实现、扩展说明、Unit TDD、CI 路由、变更历史和实际跨语言消费者。下面逐声明账本记录编辑前判定；实施结果以本节为准。

## 当前交付结果

- 已确证并修复 Memos 并发上传丢附件：真实 PostgreSQL 锁队列把两个调用排队；原版两个上传都成功返回却只留下一个附件，`memos-upload-before.log` 1 failed / 1.75s；修复相同 harness 1 passed / 2.90s（`memos-upload-after.log`）。修复将初次 memo 读取加行锁，使读取列表与追加属于同一事务锁范围；新增独立回归与 Memos release fragment。
- Mail newsletter graph 改为唯一 Source/刊物/邮件身份与定向图清理，移除 Dovecot 验收 import、全库 TRUNCATE 与数据库名子串要求。回放改在提交后的新事务、新 reconciler 上验证。根任务负责加入 DB lane。
- Bilibili 快速测试移除私有 `_project` 重复，稀疏回退改真实公开 Resolver 入口；完整索引/检索 keeper 保留。Mail 删除重复 BODYSTRUCTURE 长度断言。
- Memos 缺字节用例先下载成功，再实际删 blob，核对 404 具体 Storage blob 诊断；未知组件用例在同 HTTP 请求上验证正常 200→加入未知组件 500→删该组件恢复 200。RSS family 用例核对具体 `expected atom, parsed rss feed family` 错误；live smoke 标记 acceptance。
- 完整涉及 lane 的验证：`sources-final.log` **71 passed / 55.75s**；之后 Mail 改为新事务回放的最终验证 `mail-reopen-final.log` **1 passed / 1.63s**。7 个改动 Python 文件 Ruff lint/format 通过；改动较大文件的 Pyrefly **0 errors**（`sources-types.log`）。仅已安装依赖的 PostgreSQL/HTTP fixture 验证，不调用 live IMAP/GitHub/Telegram。
- 本 lane 测试变更净增 148 行（不追求数量），生产仅净增 1 行和注释，1 条发布说明。未删除整用例或生产测试接缝；原有独立边界保留。Capture/Telegram/GitHub 的自动化缺口仍为明确剩余证明，不能计为通过。

主要历史：Memos/RSS 的纵向数据库保护来自 `8291fd3`，异步迁移 `5b8dbf8`，CI DB 入口 `6e9a3ff`；Mail newsletter `463bece`；Local Files v2 `c1e882b`；Bilibili `3096eb7`。这些保护源于具体交付合同，不因慢、使用故障注入或检查不存在而删除。

## 编辑前结论与优先级

- **test-adequacy FAILED / P1 候选产品缺陷**：`extensions/memos/family/attachment.py` 的 `AttachmentApplicationService.create(memo_id=...)` 在持有 memo 行锁之前读取 `current_attachment_relations`。两个并发上传可都读到旧列表；稍后的 `set_memo_attachments` 把先提交的新附件当成“本次移除项”并删除。真实调用方是 `/memos/api/v1/attachments` 的 `memo` 字段。已有“同一附件被两个 memo 争用”验证不同不变式，不能覆盖同一 memo 的并发追加。需在一次性 DB、真实应用服务或 HTTP 边界用受控调度复现，若确认则把读当前列表放进 memo 锁所有权内，并记录修复前后。
- **test-adequacy FAILED / P1 执行缺口**：Bilibili 的 `test_lexical_integration.py` 与 Mail 的 `test_newsletter_graph.py` 不在 `repository_check.sh database-tests` 清单。默认 `test` 无 `INKCRE_TEST_DATABASE_URL`，两者持续跳过。前者可直接加入 DB 入口；后者只需要 PostgreSQL，却依赖 `mail_acceptance` URL 子串和 Dovecot 验收模块的全库 TRUNCATE helper，必须先改成唯一身份 fixture 与定向清理。
- **test-hygiene FAILED / P2 负向原因不充分**：Memos unknown-component 用例仅断言 HTTP 500，没有先证明相同合法请求成功，也没有排除与 resolver 无关的异常。missing-bytes 用例仅 404，没有先下载成功或核对诊断。RSS wrong-family 仅 FAILED + 空 state，没有检验 parser 报错类型/消息；这允许前置错误冒充目标拒绝。保留这些契约并加对照及具体原因，不删负向用例。
- **test-hygiene FAILED / P2 锁定私有投影**：Bilibili 快速测试在同一参数上先调用 reader，再调用 `LexicalRetrievalManager._project` 重复验证同一标签/正文；稀疏回退也走该私有函数。读取合同由真实 Resolver 入口承接，持久索引/检索风险由已有 DB keeper 承接。可移除私有 `_project` 重复步骤，保持四种记录和回退参数。
- **test-adequacy FAILED / P2 尚无当前自动化证据**：Capture Python reader 的顺序/附注分离、Telegram 的持久 offset 与失败确认、GitHub 完整快照后关系移除没有当前正式测试。它们的 API/图含义确有消费者（下面列出），并非应测试一切。建议分别用一个真实图读取、一个 fake Telegram I/O + 真 Job/DB、一个固定 GraphQL I/O + 真 Job/DB 旅程覆盖；运行外部真实服务仍需单列环境证据。

## 逐声明账本

R = 保留独立契约；F = 保留契约并修复断言/执行条件；C = 把重复部分交给已存在 owner；本 lane 没有证据足够的整用例 D。

| 文件 / 声明 | 判定 | 契约、可信回归与剩余证明 |
| --- | --- | --- |
| bilibili/test_bilibili_reader.py::test_web_records_project_without_browser | C | 4 行分别解释 User/Folder/Video/Membership 原生资料，固定独立期望，保留 Reader 的 label/text；删除同输入 `_project` 重复调用。真实索引 keeper 为下一文件。 |
| 同文件::test_sparse_identity_has_a_readable_fallback | F | 4 行固定 UID/收藏夹/BVID/aid 回退，防止未知标题变成空白；改用 ResolverManager.get 的公开 reader，保持参数。 |
| 同文件::test_malformed_v1_records_fail_instead_of_inventing_text | R | 6 行各有单个明确错误：数字身份、非 ASCII 数字、显式 null、字符串 duration、非 Z 时间、未来字段；输入经过真实模型恢复且期望独立。可强化 errors 的 loc/type 以改善诊断，但无其他 fixture 前置失败证据。 |
| 同文件::test_unknown_version_is_not_guessed | R | 未公告 v2 必须 UnknownResolverError；这是已持久未知版本不被猜解的消费边界，未知 resolver 的词法诊断另属索引容错，不重复。 |
| bilibili/test_lexical_integration.py::test_existing_maintenance_indexes_saved_bilibili_text | F | 真实 BlockService 写入→维护→retrieve_local，凭唯一 clue 验证索引/标签/证据类型；与 reader 层风险不同。纳入 DB 必跑；不能以动作返回代替查询。 |
| local_files/test_reader.py::test_location_retains_machine_path_without_remote_file_access | R | 独立 URI 字节合同：percent decode、UUID、拒绝 file URI/非法 peer/fragment。它不证明从不访问文件，只证明解释本身；名字可精确为位置解释。 |
| 同文件::test_failed_current_file_keeps_successful_body_readable | R | 原件失效仍读取上次成功正文并标示 DOCX 提取范围；缺正文因“正文关系”失败。Fake 只替代图 I/O，正文状态文字由真实 reader 生成，保留确定性。 |
| 同文件::test_v2_uses_the_body_format | R | Markdown/HTML 的实际通用 resolver 投影；v1 对同样图明确拒绝。版本合同独立，不能并入只验证 text 的正常路径。 |
| mail/test_newsletter.py::test_reading_html_preserves_both_articles_and_functional_links | R | 多文章正文、CID 图片、功能查询串不受清理损伤，移除明确隐藏/追踪/退订控制；二次清理幂等是独立不变式，非编码解码自证。 |
| 同文件::test_unknown_or_functional_links_are_not_rewritten | R | 6 行保护签名、重复参数、redirect、mailto、畸形 URL；identity 期望为“不修改”真实合同，不是无意义自比较。 |
| 同文件::test_plain_text_keeps_editorial_discussion_of_unsubscribe | R | 正文讨论 unsubscribe 保留、真正控制行删除、链接仅去 tracking；完整独立字符串期望。 |
| 同文件::test_control_only_paragraph_does_not_remove_its_articles | R | 控制只用 here 链接也应删除，但外层 table 中两篇文章保留；有具体丢内容回归。 |
| 同文件::test_declared_list_identity_is_required | R | 7 行来自已保存邮件头的 List-ID 协议身份；歧义、缺失、语法错误不创造刊物。私有 helper 可改边界，但其协议合同及参数独立，不直接删除。 |
| 同文件::test_beehiiv_requires_an_unambiguous_publication_declaration | R | 11 行保护 publication URL 来源、默认端口、路径大小写、重复头与用户/订阅者信息不成为刊物身份；与 List-ID 表不同证据来源。 |
| 同文件::test_imap_fetch_preserves_list_identity_original_content_and_alternatives | R | 固定 IMAP BODYSTRUCTURE + BODY.PEEK I/O 经真实 adapter 得到列表身份/原文/alternative_groups，再按实际阅读选择 HTML 与混合部件。`len(_walk_body_structure(...)) == 3` 是前面完整列表的重复，可删该尾部断言；主体保留。 |
| 同文件::test_body_resolver_indexes_clean_text_and_keeps_original | R | 同一原文的 lexical、default、solved、raw 四种公开消费结果不同，防止追踪链接进索引或原文被改写；不能合并成 cleaner 单元的重复。 |
| 同文件::test_image_share_controls_do_not_remove_image_articles | R | 图片式分享按钮与文章图片分离，补前面 HTML fixture 未触达的 alt-only 分支；保留。 |
| 同文件::test_nested_alternatives_select_each_level_without_losing_mixed_parts | R | 嵌套 MIME alternative/related/mixed 的独立树选择不变式，固定 part IDs，不与浅一层回归重复。 |
| mail/test_newsletter_graph.py::test_publication_grouping_replay_and_historical_body_upgrade | F | 真事务生成邮件/关系，验证精确 UID、同 Message-ID 不误合并、刊物归档、旧正文原位升级且原文不变；最后 transaction 关闭后 resolver 再读。现有全库 reset/环境子串/验收模块 import 不必要，需唯一测试来源/刊物并定向清理后进入 DB CI。 |
| mail/acceptance/test_mail_vertical.py::test_mail_collection_backfill_and_materialization | R | 真实 Dovecot→Source/Job→提交图→Resolver/Storage。J1/J2/J3 包含服务器标记、排除策略快照、精确日期回填、lazy MIME、凭据失效后的已存内容、未来删除策略。需要真实 IMAP 特性，不能用 newsletter graph 替代；现需独立 mail_acceptance DB 与已构建 Dovecot，未执行不算通过。 |
| memos/integration/test_postgresql_attachment_graph.py::test_saved_pat_replacement_and_revocation_apply_without_restart | R | DB 外部写 config 后真实 HTTP 生效/撤销，防止 Host 内存副本继续授权；与静态 PAT 格式检查无重复。 |
| 同文件::test_connection_address_preserves_public_prefix_and_requires_peer_auth | R | PAT 与 Peer JWT 的不同协议用途、持久 public prefix 及未配置 409；真实认证结果，不是 mock 调用数。 |
| 同文件::test_memos_declared_media_type_selects_exact_semantic_content | R | 7 个媒体参数是 Memos declared MIME→通用 resolver 的独立合同，真 blob 写入与新事务 download；RSS 的嗅探优先不同，不能因最终相同媒体删除。 |
| 同文件::test_orphan_attach_reorder_download_and_owned_removal_round_trip | R | 孤立附件→memo 顺序绑定→重排→移除及 blob 清理，独立事务重读；同一轮返回 attachment_ids 与 DB 关系消失共同证明完成。 |
| 同文件::test_comment_visibility_and_owned_delete_preserve_shared_reference_targets | R | 父可见性继承、直接子分页、递归自有删除和共享目标存活。直接造共享关系是明确异常/历史状态输入，删除由真生产 owner 执行。 |
| 同文件::test_comment_http_round_trip_reads_committed_resolver_graph | R | 真实 HTTP request/response DTO、路径名称、评论读写与持久删除；与服务层删除不同 transport/协议风险。 |
| 同文件::test_primary_delete_succeeds_when_best_effort_attachment_cleanup_fails | R | 注入真实 cleanup 异常后主要删除成功、残留可见，保护 best-effort 合同；保留 fault injection，不换 sleep 或真实故障。 |
| 同文件::test_missing_raw_bytes_are_404_without_fabricating_attachment_success | F | 附件 HTTP 成功写入后真删 blob，再下载应 404；缺少删除前成功下载与响应原因（可被错误 filename/路由 404 冒充），应加同路由对照和 storage-missing 诊断。 |
| 同文件::test_unknown_component_resolver_returns_500_instead_of_partial_memo_success | F | 只 500 不能确定命中 UnknownResolverError；加同请求正常图 200 对照，并在能够保留 HTTP 映射语义的边界核对具体错误原因。保留未知组件不能静默丢弃的合同。 |
| 同文件::test_concurrent_memos_cannot_claim_the_same_attachment | R | 一个 winner + 一个 AttachmentOwnershipError + 唯一持久 owner。是真 DB 竞争；当前 gather 不强制重叠，不足以证明所有调度，也不保护同 memo 的不同附件追加。 |
| rss/integration/test_feed_collection.py::test_rss_collection_reconciliation_watermark_conditional_and_manual_enclosure | R | 真 HTTP fixture→Job claim→图→Storage，覆盖条件请求、身份更改作用域、水位线、幂等更新、保留 feed 缺失旧项、媒体 materialize 与部分写失败重试；失败注入后检查具体 primary_persistence_failed、空 checkpoint 与下一次重用已写记录。不得因长旅程或 helper 私有调用删除。 |
| 同文件::test_atom_family_failure_and_automatic_enclosure_materialization | F | 保留 Atom family 拒绝、配置边界、自动附件及 MIME 嗅探合同。invalid full 已用 ValidationError + config.full + 请求不增加，足够；wrong-family 仅 FAILED/state 空，需核对 FeedFamilyMismatch 原因。 |
| rss/integration/test_live_feed_protocols.py::test_replaceable_live_feed_is_usable | R | 2 行外部 RSS/Atom 真网络 smoke，覆盖 TLS/HTTP/parser 现实可用性，不证明 DB 收入；明确 opt-in 与 live 标识。不能用 fixture 测试替代，也无需纳入普通离线门禁。 |

## 层级计划与 keeper

1. **读取/投影**：Bilibili 与 Local Files 真实 Resolver 入口拥有文字解释；Bilibili `test_lexical_integration` 单独拥有持久索引消费。移除 Bilibili 快速测试中的私有 `_project` 重复，保留所有记录/稀疏/非法参数。没有解锁需要删除的生产测试专用导出。
2. **Mail**：`test_newsletter` 拥有协议解析与无损清理；`test_newsletter_graph` 拥有数据库归档/回放/升级；`test_mail_vertical` 拥有真实 IMAP 行为。重写 graph fixture 后进入普通 DB lane，真实 Dovecot 保留 opt-in；删除 `_walk_body_structure` 的重复长度尾断言。避免为本次任务引入第二套测试框架。
3. **Memos**：保留应用服务的图与所有权 proof，以及 HTTP 的 DTO/auth/error proof。只修补无歧义负向与已确认并发追加回归；并发缺陷如复现，唯一新 keeper 应在 `test_postgresql_attachment_graph.py`，无需每层重复。
4. **RSS**：现有两个纵向 keeper 覆盖不同协议与策略风险。加强 wrong-family 错误原因；不拆长文件追求配额，不删 media 参数：RSS 来源声明 vs 字节嗅探与 Memos 上传声明是不同选择责任。
5. **无自动化区**：先按损失排序填补最小纵向契约，不复制 schema 全字段表。Capture 读取顺序/附注是已公告图协议；Telegram offset 与确认关系影响消息丢失；GitHub 完整快照事务影响收藏关系的丢失/残留。它们当前缺口应显式记录，不能声称根测试通过已覆盖。

## 无测试区域的事实入口

- Capture：`extensions/capture/resolver.py` 校验连续 material:n、唯一 annotation 和内容类型，`docs/30-unit-tdd/capture-extension.md` 声明 Python 只读；真实消费者/同契约实现包括 `impl-ts/extensions/capture/src/resolver.ts` 和 Hub capture-contract。不要要求 Python reader 测 Mac SQLite 提交恢复。
- Telegram：`extensions/telegram/source.py` 在同一 source_uow 内写图和 last_update_id，随后 `_notify`；真实调用为普通 Source Job。`README.md` 明确仅 bound_user_id 私信、offset/附件部分失败与确认，现有正式 tests 无对应路径。
- GitHub：`extensions/github/stars.py` 完整 fetch_snapshot 后 source_uow reconcile；`reconcile.py` 以 Source 行锁绑定 account_node_id，整快照替换 managed relations。真实调用为普通 collect Job，README 声明 Stars/Lists 同步；现有正式 tests 无对应路径。
- 本 lane 内所有测试接缝在 `tests/`：runtime_support 负责独立 app 挂载；DB graph fixture 和 Dovecot harness 均非生产接口。未发现因删除测试即可安全删除的生产导出。

## 验证路线

基线 DB 由根任务串行执行中；本子任务未并发运行或修改共享 DB。后续顺序：先保存 Memos 目标失败→修复 owner→同 harness 通过；再运行改过的 Mail/Bilibili/Memos/RSS 文件与保留 siblings；纳入现有 `test:database` 并使缺环境/意外 skip 不能冒充成功。构建、打包或触发编译必须使用 agent-build。活体 IMAP、Telegram/GitHub 凭据、Android Memos 真客户端均是另外的证据边界；当前未执行不应作为修复前后证明。

## 第二轮（6096624）只读判断与实施计划

重新对照原文、现有实现和完整参数。9 个测试文件在一次性 redo_sources 上独立运行 71 passed、3 个外部场景 deselected（28.60s）。上表 33 个原始声明的 R/F/C 继续有效，新增和修订项如下。上一轮「没有生产测试接缝」的结论撤回。

| 声明或支持入口 | 第二遍判定与 keeper |
| --- | --- |
| Bilibili 四个声明 | 正常/稀疏记录均由公开 ResolverManager 分派。六条错误输入应分别定位 aid 的类型/模式、null、duration、published_at、未知字段，未来版本的 UnknownResolverError 保留。 |
| Local files 三个声明 | URI 的文件 scheme、peer UUID、fragment 分别核对拒绝原因；v1 上次正文及 v2 HTML/Markdown 的输入应由真实 reader 构造，只替代关系/块读取。 |
| Memos 新增 test_concurrent_uploads_append_without_deleting_another_attachment | R。实际持锁的两个上传返回后，独立读取同一 memo 的两个附件与各自 bytes；原基点目标失败记录仍有效。其余十个声明保留表中各自的图、协议、竞争或故障契约。 |
| Mail test_mail_collection_backfill_and_materialization 与 RSS 两条采集旅程 | F。相同 Job 生命周期的启动/回收由共同 fixture 承接。Mail 未启动 JobManager 的冷进程会拒绝首个 claim，RSS 未关闭会遮蔽依赖。Mail 的真实 IMAP 与筛选/回放/延迟读取仍各有独立契约；保留。 |
| RSS / Memos media fixture | C。直接用 semantic_content_assets 返回的真实目录读取 corpus；去掉重复路径常量及比较。RSS 两个无读者的请求计数删除，feed 请求数仍有条件请求和无 I/O 检查。 |
| runtime_support.publish_extension | C。全仓调用仅传 extension/config、持久化开关和异常模式。检查共享 scripts/tests、semantic 验收后移除无人提供的 app 参数及无人读取的 PublishedExtension.app。Twitter 状态和 Memos 配置等 fake 方法仍有实际消费者。 |
| TelegramMessageResolver.create_graph | D。42d8527 自称旧 fixture 接口，无调用方/通用 draft 声明。已发布旧块的读取由固定历史 JSON 从 ResolverManager 验证；移除旧 fixture 写图捷径，保留读取。 |
| GitHubGraphQLAdapter.client | R。类可导入，替换网络客户端及其所有权属于有意义的正常外部 I/O 边界；无证据支持撤销。既有 tests/info_base/integration/test_graph_commands.py::test_github_stars_retain_node_identity_and_reject_ambiguity 单独保护复用身份/歧义，不覆盖完整多页成功/中途失败后关系替换。 |
| Capture / GitHub / Telegram 缺口 | Capture 从真实已提交图检查逆序写入、附注及坏序号；GitHub 经生产 GraphQL / Source Job 检查不完整页保留旧图、完整快照删除关系/保留实体；Telegram 从 Bot API 固定更新经 Job 检查失败时回滚、已提交后确认、下一次 offset 与授权发送者。新 keeper 分别放在各扩展目录，无需新增生产测试开关。 |

第三方账户、跨 Peer 消费和协议双替身分别按实际入口核对。二次核对确立的独立层包括 Mail/RSS 的固定 I/O 与真实协议、Memos 应用层与 HTTP、Bilibili 读取与索引、GitHub 身份与快照收敛。未发现可以整组删除且有等效剩余证明的套件。

### 第二轮实施与反证

| 新 keeper | 实际到达的契约 |
| --- | --- |
| capture/test_capture_reader.py::test_material_order_and_annotation_survive_reopening | R。提交反序关系，重新打开并经正式 reader 核对甲→乙→用户附注；提交删除序号 0 后，同一入口 refresh 必须明确拒绝缺口。 |
| 同文件::test_material_limit_matches_the_capture_graph_contract | R。Hub capture-contract 规定单次最多 128 份，TS reader 也拒绝超过 128。129 条实际关系在 6096624 得到 DID NOT RAISE，修复后拒绝；删除最后一条后 128 条仍能完整读取。 |
| 同文件::test_screenshot_has_readable_text_and_keeps_image_facts_for_search | R。Hub 定义 attachment→core.image、可选文件名、MIME 与存储 -4；TS 同职责 reader 默认返回文件名/MIME。真实 PG 图片、父图及 Blob 在旧实现的普通文本入口抛出 UnsupportedResolverCapability(core.image.v1, text)，整份采集无法阅读；新的默认展示可读，lexical 仍读取图片 bytes 返回 media type/width。 |
| github/test_star_collection.py::test_partial_page_preserves_last_complete_stars_and_lists | R。仅替换 HTTP 传输，初始完整快照生成三种成员关系；后续空 Stars 加未完成的 Lists 第二页报出独立固定错误，现有树未变；完整空快照后相同 Account/Repo/List Block ID 仍存在且三种关系均已移除。注入遗留 contains 后此 keeper 红。 |
| telegram/test_inbox_collection.py::test_published_message_v1_still_reads_from_its_stored_json | R。来自已发布格式的固定 JSON 经 ResolverManager 解出 caption 和 media_type；独立证明仅删旧 fixture 写图接口仍可读取存量。 |
| 同文件::test_failed_primary_retries_from_committed_offset_before_success_reaction | R。生产 SDK 解析固定 Bot API 响应，独立事务观察发送失败通知/成功点赞当时的内容和持久游标；中途游标写入失败须撤回正文，跳过未授权用户，下一次从 9 重试，第三次从 10 开始。移除保存反馈时 keeper 红。 |

原表的 Bilibili 正常/稀疏四种情况现在都用公开分派。加回放行 future 字段的变更后，只有该参数行错误地成功；Local Files v2 故意只接收 core.text 后 Markdown/HTML 两行均因类型错误变红。新旧读者同时支持的 v1 正文继续通过。两个新增来源 keeper 的破坏和这三项旧保护共 5 failed、17 passed，进程退出即撤销替代。Capture 两个产品缺陷分别有未修代码的实际失败记录。

- 保留独立的 Mail MIME、阅读清理/刊物恢复、Memos PAT/附件/HTTP/持久图、RSS/Atom、Bilibili 索引；稳定 fixture 与状态故障注入仍有调用方。shared runtime helper 的已有 CalDAV/Twitter/Memos/semantic 调用只依赖 client 和 unpublish。往届 Telegram A1/A3/A4 场景的游标/确认、GitHub Stars/Lists 同步及 Capture 多材料消费本次有可重复验证，真实 Telegram/GitHub 服务及 Mac 写入仍由相应环境验收。
- 联跑改动 owner、共享 helper 的邻组和 GitHub 图命令，102 passed、3 个 opt-in（Dovecot、两个 RSS/Atom 外部源）deselected，55.77s。Pyrefly 0 diagnostics，Ruff lint 通过。原始记录本地可读 redo-sources-baseline.xml、redo-sources-final.xml、redo-sources-mutations.log、redo-capture-before.log、redo-capture-screenshot-before.log。


独立复核补回 capture/test_capture_reader.py::test_capture_needs_material_or_an_annotation。Hub 同时允许纯附注且禁止空材料+空附注。旧版成功读出空记录（redo-capture-empty-before.log），新实现应明确拒绝；为同一根提交纯附注后，新 reader 可读。截图 keeper 增加无文件名时的 MIME 对照。

Mail 的正式 J1–J3 用普通 Core 角色运行时，旧 fixture 的 TRUNCATE … RESTART IDENTITY 因 crons_id_seq 所有权提前失败（redo-mail-live-sequence-before.log）。现用独立迁移模板的私有 DB，退出时释放，保留真实 IMAP/QRESYNC、Job、图、MIME bytes 和回放。J4 的 impl-ts/tests/e2e/mail-info-base.spec.ts 按固定 Message-ID 查找尚无 content 的 inline，然后向活着的 IMAP Source 下载；J3 收尾已经 materialize 此 inline，模块退出也关闭 Dovecot。独立浏览器场景需要保有其执行期间的收件服务和待下载 fixture。没有找到消费本 pytest 最终数据库的调用。两条可替换 RSS/Atom 公开协议 smoke 另在 NASA RSS 和 pytest GitHub commits Atom 都通过（2 passed / 2.09s）。

最后的定向验证：Capture 4 个 keeper 全过（10.91s），普通截图带/不带文件名及词法 bytes 均实际读取。真实 Mail J1–J3 在 Dovecot 2.4.4 上通过（39.98s），专用非管理员 Core 角色访问临时迁移库，错误密码下未下载部件的原因明确匹配 authentication failed。测试进程仍用原 DovecotHarness 与 IMAP 操作；临时 launcher 将它生成的配置及端口接入 Docker 官方固定 digest 723e3392…，映射容器内用户与临时目录。该容器、SSH 端口、临时启动文件、测试库已经关闭/删除。没有运行另一仓的 J4 浏览器。

Memos 的 5 个 HTTP 用例也改为专有数据库，覆盖 publish_extension(persist_config=True) 写入全局安装配置、connection 修改 Peer.config 的真实副作用。普通图与媒体 case 仍按 block/blob ID 清理。所有 5 个 HTTP keeper 通过（34.48s）。源码差异中，Capture 对空图、数量、默认图片文字的修复增加 6 行；Telegram 旧 fixture 接口减少 8 行。新增 2 条扩展发布说明。任务控制、原始日志和发布说明不计入源码行数。

外层库另置标记并记录既有 Peer 配置，运行 saved PAT 与 connection 两项（2 passed），回读确认标记和全部既有 Peer 配置保留，随后恢复标记的原值。见 redo-memos-isolation-witness.log。

独立复核后，RSS 的两个本地采集场景改用私有数据库，删除曾遍历整个连通分量的清理。修复前，在 feed 上连入另一所有者的记录后该记录被删，且额外留下 1 个竞争 Job；修复后同一控制看到外层哨兵仍在、Job 增量为 0，两个场景均过（49.94s）。对不存在的 enclosure，保留失败 ID、状态和确切原因；把未找到替换为数据库故障后在原因断言处失败。Bilibili 四类协议输入对照 Hub 定义使用固定 resolver 名，改错 Video 注册仅令视频参数因 v1 ID 无法解析而失败；未变异的 reader 15 项通过。
