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
