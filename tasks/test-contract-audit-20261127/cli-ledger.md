# CLI 契约审计

## 基线与来源

基点 `9f75cbb`。`cli/tests/test_extension.py` 是 CLI 唯一现有测试文件，99 行、3 个声明；`pdm run -p cli test` 全部通过（0.077 秒测试时间）。CLI 是独立发行的 REST 消费者，不拥有 Core persistence 或 Job execution。

已完整阅读本文件所有参数行、`cli/src/inkcre_cli/` 的连接、入口、HTTP、输出、Extension、信息读取与 Job owner、`cli/README.md`、`cli/docs/development.md`、CHANGELOG、Core REST 内容 producer 和文档，以及用户连接/检索指南调用方。历史 `10a5384` 建立独立 CLI，`15a4cce` 修复 Extension v2 并首次加入当前三项测试；当前 CI 的 `cli` job 经 `cli_publication.sh check-build` 运行独立项目 `check` 和构建。

方法实际采用公开 prune-tests、abyss `425a286` 的两个方面、OpenClaw test-audit SKILL/CAMPAIGN/references/audit，以及公开 writing-tests、boundary-scenarios、fixtures、openclaw-testing 的适用内容。OpenClaw 的 pnpm、Vitest、行数配额、Crabbox 等专属机制不适用。测试层以 CLI 自己承诺的契约归属决定，不因 Core 也有 HTTP 覆盖而重复或删除。

## 现有声明账本（编辑前）

| 声明 | 判定 | 契约、可信回归及剩余证明 |
| --- | --- | --- |
| `test_select_uses_v2_body_and_optional_target_peer` | R | 真实 Click→HTTPX request 的 v2 路径、distribution/version、可选 peer；错误沿用旧 install 或遗漏 route_to_peer 会失败。两行覆盖本机与异构 Web Peer，非重复。独立 fixture receipt 的 enabled=false 保护 CLI 不隐式启用。 |
| `test_unselect_preserves_status_receipt` | R | DELETE selection 和目标 peer，以及完整状态回执透传；回归到旧 uninstall 或丢失 peer/状态即失败。它只证明 CLI 请求/输出，不声称证明 Core 保留共享信息。 |
| `test_config_schema_comes_from_current_peer_not_shared_identity` | R | schema 通过当前 Peer 的 v2 status 发现；shared row 不再有 schema。status 命令另一个入口的完整状态回执是独立用户入口，保留同一个 fixture。若改回共享身份 GET 即失败。 |

无 C/D；未发现 test-only 生产 seam。`Invocation._client` 是生产生命周期缓存；测试替换 HTTP transport，不向生产新增接口。

## 层计划（编辑前）

- 保留 `test_extension.py` 作为 Extension 请求转换的主要 owner；不再重演这些路由。
- 新增 `test_cli.py`，通过独立 CLI 进程和一次性本机 HTTP fixture，验证发布的 CLI 入口、凭据选择、错误通道、部分结果、二进制文件交付。每次命令重新读取连接文件；不使用内存对象证明持久化。
- 连接 keeper：set/use/list/get/delete 后跨进程读回；一次性 `--connection` 不改变默认；网络 fixture 验证选中连接的 JWT，而非只检查保存字段。新进程读取失败、默认覆盖、密钥泄露是可抓回归。
- 退出契约 keeper：本机输入错误精确到字段校验；HTTP 401 与成功读到 failed Job 分别得到 1 和 0；partial batch 保留成功项并返回 1。现有三项只调用 Click group，不经过 `main()` 错误映射。
- 内容 keeper：独立手写 MIME fixture（不用 Core producer 计算期望），包括 RFC 6901 `/`/`~`、数组索引与普通 null；CLI 导出后移动目录并重开读取 bytes，HTTP 请求计数保护同次响应交付。Core producer 仍拥有相反方向的协议责任。
- 首先运行新增测试；每个新增 keeper 使用暂时的可信生产变异确认能失败，再逐字恢复生产文件。若实际发现产品缺陷，单独保留修复前失败、修复后通过记录并通知主任务。
- 不把所有 endpoint 变成机械枚举测试；schema projection 暂不扩大。补读 Job owner 和开发文档后，总预算覆盖慢 HTTP 响应是明确且未被其它测试触及的独立 CLI 责任，因此补充一项事件控制的慢响应验收。真实 Core 配置/数据库/第三方能力仍由现有 provider 与端到端验收拥有。

## 实施与证据

保留原有 3 项，新增 `cli/tests/test_cli.py` 的 6 项真实命令进程验收；测试/support 从 99 行增至 371 行（增加 272 行），生产源码增加 0 行。未发现 CLI 产品缺陷，未删除现有保护，也没有新增生产测试接口。

| 新 keeper | 实际拦截的可信变异 | 失败证据 |
| --- | --- | --- |
| `test_connections_survive_new_processes_and_temporary_selection` | 连接文件替换写入被遗漏 | 新进程 list 返回 `default=None, connections={}`，与已 set 的两个连接不符。 |
| `test_invalid_connection_reports_local_field_error_without_overwriting_saved_value` | URL 从 HttpUrl 退化为无校验 str | 错误输入退出 0，而不是预期字段校验失败的 2。正常版本精确观察 `loc=[base_url], type=url_parsing`，并重开旧连接确认未覆盖。 |
| `test_http_failure_and_failed_job_have_distinct_exit_contracts` | CommandError 错误归为成功退出 | HTTP 401 得到退出 0 而不是 1；正常版本保留远端诊断，随后读取 status=failed 的 Job 仍为 0。 |
| `test_partial_batch_keeps_full_success_and_errors_on_separate_stream` | get 丢失 partial_failure 判定 | 含错误批次退出 0；正常版本 stdout 保留完整超过摘要预算的结果，stderr 标明逐项错误，退出 1。 |
| `test_binary_export_remains_readable_after_directory_move_without_refetch` | 显式导出写绝对引用 | 便携性断言失败；正常版本移动目录后重新读出独立 fixture 的原始 bytes。纯 octet-stream 另行覆盖直接二进制分支。 |
| `test_job_wait_budget_cancels_inflight_read_without_aborting_job` | 把用户观察预算替换为固定 30 秒（忽略本次 0.5 秒预算） | 10 秒进程保护超时；正确版本在服务端尚未返回任何响应时按自己的预算退出，诊断为尚未取得记录，且只有一次 GET，没有 abort。HTTP fixture 使用 Event 持有响应至命令结束，不 sleep，不伪造 timeout 响应。 |

六项变异均因目标回归失败；每次在 `finally` 恢复原始 bytes，并核对 SHA-256 相等。没有把这些临时变异称作历史产品缺陷或修复前失败。新增 fixture 以 RFC 6901 手写 MIME 字节为 oracle，不调用被测 Core producer 生成期望，也不预置任何 CLI 导出结果。

最终恢复后 `pdm run -p cli test`：9 passed，3.863 秒。`pdm run -p cli lint`、`pdm run -p cli format:check`、`git diff --check` 通过。原有 Extension 3 项保持不变。最终 typecheck/build 由主任务处理已确认的 `.worktrees` 路径默认排除问题；此处未修改 pyproject、锁文件或共享脚本。

## 结论与边界

`test-hygiene: PASS`：每个现有和新增 keeper 均有独立契约，未发现可证实的冗余删除；强制清理会失去当前只有一处的 Extension v2 证明。

`test-adequacy: PASS（本次交付的 CLI 验收范围）`：在本机隔离 HTTP/文件边界证明命令进程、连接持久化和选择、JWT wire、错误通道/退出码、部分结果、二进制可搬移导出和慢响应的 Job 观察预算。它不证明真实 Core 业务状态、生产网络或 PyPI 安装。完整阅读其余命令及 schema projection 后，未发现需要本次修复的明显产品错误；动态 schema 重定位、混合 recall 等路径仍不能算已有自动化契约覆盖，后续修改各 owner 时应补它们的直接证明，而非机械枚举所有 endpoint。独立发行构建保持现有 CI owner。

## 对 6096624 的第二遍审查及层级计划

逐一复核两个 CLI 文件全部九个声明和参数行。连接重开与临时选择、字段错误保留旧值、失败 Job 与 HTTP 错误、部分批读、等待期限、可搬移 MIME 导出的六项保留，其独立失败控制见上表。原三个 Extension 声明均标 C，迁入同一个已覆盖 CLI 进程、真实请求和结果输出的 HTTP fixture。保留两类 Distribution/目标 Peer、GET 与管理 status 的区别、select/unselect 的方法/路径/回执和 config schema 选择。移除构造并替换两次 HTTPX client、直接写 Invocation._client 的测试装置。生产 Invocation._client 仍由命令创建/复用/关闭，不能删除。

充分性复核发现两个公开承诺缺单独证明。其一是 source create --schema 应把由 selector 提供的 type 去掉，并让调用者能在输出文档中解析 owner 的局部引用。其二是 recall 的一个 HTTP 请求失败时，另一个模式仍可独立取回并交付结果。现有 get partial case 的两项都由一次响应给出，无法检出 recall 遇首个 HTTP 错误就停止。两项均可用同一 CLI 进程及本地 HTTP 装置验证。响应按请求路径选择，允许互不依赖的发现请求改变顺序。CLI 发行入口、REST 消费者和变化历史已核对，采用相邻 CLI check 与定向控制验证。

CLI 的十个进程场景最终用时 4.45 秒；相邻 lint、format、类型均通过。以独立源码副本分别改错 distribution、丢弃 unselect 回执、把 config schema 读回共享身份、忽略局部引用重定位、首个 recall 失败就停止，五次均在目标断言失败（`redo-cli-controls.log`）。模拟 REST 回执使用 ExtensionPeerStatus 的层级、合法 Peer UUID 和 semantic 响应的 profile/metric/matches。
