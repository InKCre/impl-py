# 审计结果与验证证据

## 结论

本次按生产责任区审阅完整维护测试、调用方、CI 与验收入口，修复三个已复现的产品缺陷，以及未执行、失真断言、夹具污染和执行生命周期问题。独立复核找到两个覆盖迁移遗漏（整理工具返回的持久 ID、历史观测身份及空事件名），已在真实 keeper 中补回并通过破坏性控制。

`test-hygiene: PASS`：本次删除或收敛的断言均有明确剩余证明；未按静态、慢、负向或接缝标签删除。`test-adequacy: PASS（本次改动及准入范围）`：本地证据覆盖实际持久化、独立重开、事务失败、真实并发与 HTTP/进程边界。完整镜像与远端环境证明仍按下表记录，不能从 mock 或 skip 推断。

## 产品缺陷的失败与通过对照

| 缺陷及 owner | 修复前目标失败 | 修复后 |
| --- | --- | --- |
| 图路径：`GraphNavigationRetrievalManager.find_path` | 3 Block、10001 Relation，较早直达边被一页较新平行边遮住；返回 `not_found`，应为 `found` | 同 harness 返回两端点、原直达 Relation 的单跳最短路；1 passed；完整知识 lane 26 passed |
| Memos：`AttachmentApplicationService.create` | 用真实 PG 行锁把两个追加同时排队；两请求成功却仅保留一个附件；1 failed（1.75s） | 初次读 memo 即锁行，读取列表与追加同一事务；1 passed（2.90s），来源 lane 71 passed |
| 扩展状态：数据库 trigger | 修好测试基础设施后六例中仅 state guard 失败：非空状态 DELETE `DID NOT RAISE IntegrityError`；1 failed / 5 passed | 追加迁移 `c7f2a6d9e410` 恢复既有函数的 trigger；相关 owner 12 passed；既有 revision 未改、现存行不重写 |

原失修迁移测试的重复 `dbname`、不存在的 session factory 与错误前置 fixture 不属于产品红灯，上述 state guard 对照是在它们修复后取得。

## 执行证据

基点为 `9f75cbb7ffc3bb92dedcfc8d27a2bebff9848ec0`。本机 Python 3.12 / PDM 2.28.0，所有破坏性测试仅使用本任务创建的 pgvector PostgreSQL 17 容器；不同分区独立数据库。临时 Docker 位于既有 SSH context，端口经本任务转发，本地/共享开发和 Preview 数据库均未使用。

| 验证 | 已观察结果 | 证明边界 |
| --- | --- | --- |
| 基线全部已知 Python 测试目录（不含独立 CLI） | 无数据库 224 passed / 85 skipped；显式一次性数据库 291 passed / 5 failed / 13 skipped | 三个失败是 Job 未启动；两个是 CalDAV 伪图片夹具污染后续 Twitter 全库索引；真实 provider 与原生 migration 缺环境分别记录 |
| 最终默认 `pdm run test` | 228 passed / 89 skipped / 6 deselected，18.58s | skip 是未选择数据库/环境；deselected 是明确 acceptance，不当作通过 |
| 新 schema 的 `pdm run test:database` | 86 passed / 0 skipped / 1 credentialed deselected，77.44s | 新建 `audit_final`，实际迁移到 c7f2a6d9e410；包含迁移、来源、语义、CalDAV/Twitter持久化 |
| 后补 Peer HTTP keeper 与相邻测试 | 4 passed，2.88s | 真实 DB discovery→HTTP，明确未执行可换候选；收到请求后断开不会重复派发；两个 mutation 均失败 |
| 独立 CLI | 9 passed；最终子范围3.863s | 真实命令子进程、连接重开、JWT、错误通道、部分结果、独立MIME fixture、搬移导出、慢HTTP观察预算 |
| 发布恢复／App／镜像 | 11 passed，3.68s | 实际保存与恢复候选字节、篡改拒绝；外部CLI替身不证明真实Heroku/Neon联通 |
| 静态和治理检查 | foundation、format、lint、database boundaries、Pyrefly、actionlint、migration integrity、diff check通过 | Pyrefly 0 diagnostics，101项既有suppression未增加；原依赖版本未升级 |
| 构建与完整 CI | 待 PR 的 exact-head CI | 本机 `agent-build` 共享队列等待后已取消本任务尚未启动的两个排队命令，不把未执行写为通过；不干扰其他任务构建 |

原始运行日志、JUnit 和一次性 mutation 脚本留在本任务目录；这些临时材料及外部材料副本不进入提交。逐声明决定、keeper 与失败模式在六个 `*-ledger.md`；它们是本次审计记录，不是新的长期产品权威。

计量口径为 `tests/`、`scripts/tests/`、`cli/tests/`、`deploy/integrations/chatgpt/tests/` 的 Python 测试及支持文件：基线 75 文件 / 15328 行 / 202 个 test 声明，候选 80 文件 / 16744 行 / 215 个声明。参数化用例数另由 pytest 报告；未把运行环境不同的通过数当性能改善。生产逻辑和新迁移合计增加 55 行、删除 11 行，无新增仅为测试服务的生产接口。这轮净增加覆盖，不以删行作为成功标准。

## 保留与收敛

- 保留独立 SDK、数据库约束/ACL、历史迁移、Loki协议、CalDAV字节与HTTP冲突、Twitter合并、发布路径、CLI、Job/Cron和Provider验证。跨层风险分别由其owner验证。
- PostgreSQL观测从固定mock行/SQL形状转为真实写入→关闭writer→新reader→过滤/截断与历史fixture。恢复了旧ID/事件名、筛选、literal文本控制。
- 整理从私有helper和直接工具调用收敛到真实Agent loop，保留方向、局部ID、持久ID、关系和上下文；生产者图/词法重复场景合并。Bilibili去掉重复私有 `_project`，完整持久化索引keeper保留并接入DB入口。
- 空Twitter submit迁到真实HTTP→DB keeper；无selection启动与disabled合并参数表；仍验证零Registry/package副作用。CalDAV精确清理实际创建的图和blob，协议DELETE只撤下发布的语义不变。
- 默认发现所有维护测试目录，数据库入口显式准入上述keeper；发布脚本/ChatGPT默认已执行，移除CI重复命令。真实供应商验收显式排除，专门命令继续保留。

## 实际应用的方法

已完整读取并应用公开 [prune-tests](https://github.com/caldoai/skills/blob/main/skills/prune-tests/SKILL.md)、[abyss 425a286](https://github.com/BeautyyuYanli/abyss/commit/425a286) 的两方面审查指导、[test-audit](https://github.com/openclaw/openclaw/blob/main/.agents/skills/test-audit/SKILL.md)、其 [CAMPAIGN](https://github.com/openclaw/openclaw/blob/main/.agents/skills/test-audit/CAMPAIGN.md)、[audit reference](https://github.com/openclaw/openclaw/blob/main/.agents/skills/test-audit/references/audit.md)、[proof policy](https://github.com/openclaw/openclaw/blob/main/.agents/skills/openclaw-testing/SKILL.md)、[writing-tests](https://github.com/openclaw/openclaw/blob/main/docs/help/testing/writing-tests.md)、[boundary scenarios](https://github.com/openclaw/openclaw/blob/main/docs/help/testing/boundary-scenarios.md) 和 [fixture guidance](https://github.com/openclaw/openclaw/blob/main/docs/help/testing/fixtures.md)。使用基线、逐声明账本、第二遍层级计划、独立保留复核和定向控制；未套用 OpenClaw 的秒数/行数配额、Vitest或发布工具，也未声称取得未公开原版skill。

## 剩余边界与风险

真实 AI 质量/人工评价、Dovecot IMAP、真实 X/userscript、移动 CalDAV/Memos 客户端和真实 Heroku/Neon/Loki 未重新验收。Capture Python读取、Telegram确认/offset、GitHub完整快照关系替换和CLI动态schema/混合recall没有在本次建立新的自动化，后续改变各owner时应取得对应证据；本轮未改这些产品路径。

图路径现在扫描完整关系页，高重数图会比旧错误实现多读页；Block与跳数预算保持原意。Memos追加在同一Memo行上串行化，换取不丢附件。新增迁移可单独downgrade撤下trigger，不改写数据，但会重新暴露原有状态保护缺陷；其他修复可逐提交回退。迁移验证调用真实 `provision_roles`，会写集群级角色配置，必须复用初始化密码并仅针对一次性集群，fixture本身不构成共享环境防误用保障。
