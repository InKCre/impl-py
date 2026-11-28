# 测试与验收审计结果

审计基点为 `9f75cbb`。`6096624` 是 PR 第一轮通过 CI 和 Preview 的提交。复核从原基点的测试声明、生产调用者和验收入口重新对账，逐区核对第一轮的保留理由，补做测试专用生产代码清理、独立契约和真实执行边界的验证。

[原始清单](baseline-inventory.md)列出 56 个有测试声明的模块及各文件的实际基线。六个责任区分别记录声明和参数行的处理、删改后的保护位置、变异失败及执行边界：[来源](sources-ledger.md)、[协议](protocols-ledger.md)、[执行](execution-ledger.md)、[知识](knowledge-ledger.md)、[交付](delivery-ledger.md)、[CLI](cli-ledger.md)。各账本的第二轮章节更新第一轮结论。

## 已复现的产品缺陷

| 调用方看到的错误 | 同一场景在修复后的结果 |
| --- | --- |
| 3 个 Block、10001 条 Relation 的真实图中，较新的平行边挡住了较早的直达边。路径查询返回 `not_found`。 | 找到既有直达 Relation，并返回单跳最短路。完整扫描可能需要多读关系页。 |
| PostgreSQL 行锁释放后，同一 Memos 笔记的两个并发上传都返回成功，独立事务只读回一个附件。 | 读取后追加的过程持有同一笔记行的锁。两份附件的 ID 和下载字节均可读回。 |
| 实际从历史版本升级后，删除非空扩展共享状态成功，预期的数据库约束没有触发。 | 追加 revision `c7f2a6d9e410` 恢复 trigger，数据库拒绝该 DELETE；清空状态后可删除。历史 revision 保持原样。 |
| 带有大量 `ß` 前缀的文本正确命中 `QuarzFund`，调用方收到的词法摘录却没有命中词。 | 转回原文坐标后，同一查询继续返回原 Block，摘录含原文中的 `QuarzFund`。 |
| 真实 Capture 图有 129 条材料关系时，Python 分发仍按正常采集读取。 | 读取入口拒绝第 129 条，128 条边界可完整读取，与 Hub 和 TS 读者契约一致。 |
| 材料和附注都为空的 Capture 曾被读成只有时间的采集。 | 空图按契约拒绝，合法的纯附注采集仍可重新打开并读取。 |
| 含截图的 Capture 在普通文本读取时抛出 `UnsupportedResolverCapability(core.image.v1, text)`，整份采集无法阅读。 | 普通读取展示文件名或 MIME；词法读取仍从原图片字节取得检索信息。 |
| 原生浏览器在已连接账户页提交断开表单时发送 `Origin: null`，收到 403。 | 账户页使用同源 Referrer Policy。相同 Helium 隔离会话提交后回到账户页，刷新后仍显示已断开，接收端的同源校验继续生效。 |

迁移夹具用错数据库名、Job 未启动、测试留下坏媒体或测试缺少角色权限分别按测试问题处理。以上产品对照均在消除相应夹具问题后取得。

## 清理的生产代码及剩余证明

- 扩展 Host 去掉仅用于替换 Registry、包消费者和 origin 的三个注入参数、两种注入协议，以及只转发方法的适配器。启动验收使用真实 Registry 客户端解析响应；多 wheel 在隔离目录分阶段下载并读回，失败时尚未开始安装。测试替换网络和包安装边界。
- Peer 去掉用于固定测试顺序的随机源成员。两个真实本机端点区分明确未执行、普通 503 和收件后断线，检查是否发生第二次执行。Job 测试经调度器进入处理函数，观察取消、追踪和跨事务终态。
- Telegram 移除无写入调用者的旧 fixture 接口，已发布 JSON 用正式 reader 验证。Twitter 写入去掉仅供测试读取的内部 ID 映射，产品调用者保留实际变化项的回执，测试通过持久图和真实 HTTP 查询身份。固定来源输入保留在 I/O 边界。
- 移除生产目录中供 CI 计算 reset 指纹的实现。验收以独立 SQL 获取初态，在专用库写入哨兵，真实重置两次均须复原。恢复改在首次 init 之前验证导入结果。旧流程实际上接受空输入；新流程在缺少导出的角色或迁移时正确失败。
- 独立 CLI 原来三个直接替换 Click 客户端的用例迁到真实命令进程和本地 HTTP。保留目标 Peer、分发、方法、查询、回执和退出码；补齐动态 schema 局部引用与混合召回部分成功。五个第二轮反例均触发预期失败。
- 全库检索与整理场景从已迁移模板克隆私有数据库，以 Core 身份运行。移除整表清空与沿关系图清理数据，独立复查发现外层哨兵在运行后仍在，临时库均已回收。PG checkpoint 会增加测试时间。真实迁移/建角色的验证继续要求专用集群及其既有密码。
- CI 用一次性 PostgreSQL 执行 pytest 默认发现的完整集合，CLI 单独运行。取消易漏新目录的数据库专用路径清单，凭证验收显式选择。原 `bash -n scripts/automation/*.sh` 只解析第一个脚本；把语法错误放在第二个文件，旧命令成功，新逐文件命令以 2 退出。

## 运行与边界

原基点显式发现全部仓库测试时，无库 224 通过、85 跳过；一次性 PostgreSQL 291 通过、5 失败、13 跳过，另有 CLI 3 通过。五个失败分别来自未启动的三个 Job 场景，以及两个受前一协议测试残留数据影响的图场景。

第一轮实际 CI 运行了 core/CLI/各扩展打包及仓库测试、镜像、PostgREST、reset、Web ready 和导出恢复。其 PostgREST 清理曾错误删除非空状态；修正后，目标 DELETE 返回具体 SQLSTATE、错误与保留记录，空状态可以删除。移除 trigger 时控制失败。[第一轮完整 CI](https://github.com/InKCre/impl-py/actions/runs/45999523367)

第二轮各区使用任务专用 PostgreSQL 17 与独立数据库。生产网络以外还实跑了本地 HTTPS 浏览器账户旅程、公开 RSS 与 Atom 服务。真实 X/Twikit 服务、Telegram/GitHub 账户、移动客户端、真实 AI 模型和人工质量、生产 Heroku/Neon/Loki 未据此验收。打包及镜像端到端结果随 PR 当前提交记录。[PR 检查](https://github.com/InKCre/impl-py/pull/201/checks)

独立复核又找回以下保护。

- 只改 Twitter backfill 的策略为替换时，写入新语言仍成功，但独立读回看到原正文被预览覆盖；新断言因正文差异失败。官方书签的普通帖与长文两种正文分别有独立样本。Peer 端口未监听时，真实 TCP 拒绝被识别为明确未执行；将它改判为结果未知会失败。
- Bilibili 四类 v1 Block 使用 Hub 规定的固定协议名构造。只把生产视频标识改名时，四行中只有视频一行因旧 ID 无法解析而失败，撤去变异后全部 15 行通过。
- RSS 旧清理沿关系走进其他所有者的 Block，将其删除，还遗留一条竞争 Job。以前后同一外层哨兵与 Job 数量查验。Memos 的五个 HTTP 场景原来可覆盖已有 PAT 和 Peer 配置，也移入私有库并核对外层配置。
- 两套临时 PostgreSQL 上，旧恢复检查接受夹带真实 job_types 记录或版本错误的导出；新检查在首次初始化前分别以业务表非空或契约版本不符拒绝。重置验收除核对拒绝原因还要重读哨兵：移除清空前的身份校验后，错误文本仍对，但哨兵已丢；删除确认词校验后，错误请求重置成功。干净备份与两次合法重置均通过。
- Mail 在实际 Dovecot 2.4.4 上经过收件、回填、读取和显式 MIME 物化，39.98 秒通过。旧测试用普通 Core 身份执行全库 `TRUNCATE ... RESTART IDENTITY`，提前因 sequence 所有权失败；私有数据库消除了该前置条件。Web J4 需要尚未物化的 inline MIME 和仍可用的 IMAP 服务，本次未执行该浏览器用例。

## 计量与门禁

同一口径统计 `tests/`、`scripts/tests/`、`cli/tests/`、ChatGPT 集成测试目录内的 Python 测试和支持文件。参数行只在运行结果中计数。

| 版本 | 文件 | 代码行 | 测试声明 |
| --- | ---: | ---: | ---: |
| 原基点 9f75cbb | 75 | 15,328 | 202 |
| 第一轮交付 6096624 | 80 | 16,744 | 215 |
| 第二轮实施 7a0a32d | 86 | 16,817 | 225 |

相对第一轮，`app/`、`inkcre_core/`、`extensions/`、`deploy/`、`scripts/` 中除测试外的 Python 代码增加 48 行、删除 218 行。两份独立验收 SQL 不计入 Python 指标。

第二轮整合运行在一次性 PostgreSQL 上 328 通过、6 个外部验收排除，263.74 秒。随后独立复核新增的空 Capture 和 TCP 连接拒绝场景以及改动过的 keeper 均定向通过。最终无数据库默认集合 233 通过、97 跳过、6 排除；独立 CLI 项目检查 10 项通过。全仓 format、Ruff、Pyrefly（0 diagnostics）、数据库边界、迁移完整性、锁文件、发布意图、actionlint 与 diff 检查通过。最终提交的整套数据库、打包、镜像及 Preview 结果见 [PR 检查](https://github.com/InKCre/impl-py/pull/201/checks)。

## 方法来源

逐文件建立基线，读生产调用者和历史，标记每个声明及参数行，按独立契约做第二遍分层计划，再迁移断言、删除确认无消费者的接缝、做独立保留复核与定向变异。候选的删除量与测试数量均未作为目标。判断分别覆盖测试卫生与充分性。使用公开整合材料；OpenClaw 特有的运行时间、行数指标和工具没有移植。[prune-tests](https://github.com/caldoai/skills/blob/main/skills/prune-tests/SKILL.md)、[abyss 425a286](https://github.com/BeautyyuYanli/abyss/commit/425a286)、[test-audit](https://github.com/openclaw/openclaw/blob/main/.agents/skills/test-audit/SKILL.md)、[CAMPAIGN](https://github.com/openclaw/openclaw/blob/main/.agents/skills/test-audit/CAMPAIGN.md)、[audit reference](https://github.com/openclaw/openclaw/blob/main/.agents/skills/test-audit/references/audit.md)、[testing](https://github.com/openclaw/openclaw/blob/main/.agents/skills/openclaw-testing/SKILL.md)、[写测试](https://github.com/openclaw/openclaw/blob/main/docs/help/testing/writing-tests.md)、[边界](https://github.com/openclaw/openclaw/blob/main/docs/help/testing/boundary-scenarios.md)、[夹具](https://github.com/openclaw/openclaw/blob/main/docs/help/testing/fixtures.md)。
