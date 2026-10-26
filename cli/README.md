# inkcre-cli

InKCre 的独立命令行界面，通过 Core REST API 检索信息、读取 Resolver 内容和管理部署。
它不安装 Core、不访问数据库，也不是执行 Job 的 Peer。

```sh
python -m pip install inkcre-cli
inkcre-cli --help
inkcre-cli connection set personal --input connection.json
inkcre-cli recall 'Agent 工具' --mode lexical --json
inkcre-cli get block:42 relation:8 --json
inkcre-cli resolver invoke block:42 --method get_solved_content
```

connection.json 包含 `base_url` 与 `jwt_secret`。默认连接文件是
`~/.inkcre/cli/connections.json`；可用 `--connections-file` 指定隔离文件。
这里的地址是 Core REST 入口，不是 PostgREST 或 MCP endpoint。
首个连接自动成为默认连接；`connection use personal` 修改默认值，`--connection NAME` 只选择本次接入。

`--help` 离线可用；有结构化输入的命令提供 `--schema`，从当前 Core 发现准确合同。
使用 `--input FILE`、`--input -` 或 `--input-json JSON` 提供输入。
`--json` 输出完整紧凑 JSON；二进制内容保存为文件，不做 Base64 编码。
`--output-dir DIR` 显式保存完整结果，stdout 返回入口文件路径。

## 发现与管理

先发现 exact type、method 或 schema，再提交它所描述的输入。例如：

```sh
inkcre-cli source types --json
inkcre-cli source create --type SOURCE_TYPE --schema
inkcre-cli source create --type SOURCE_TYPE --input source.json
inkcre-cli source collect 42 --input-json '{}'
inkcre-cli job wait 17 --for 10s --json
inkcre-cli job abort 17
inkcre-cli cron create --job-type core.source.collect.v1 --schema
inkcre-cli agent tools --json
inkcre-cli ai models --json
inkcre-cli config schemas --json
```

这里的 ID 仅为示例，应使用实际创建回执或查询返回的 ID。`source collect`、`source backfill` 和
`organization ruminate` 创建 Job，而不是在 CLI 或接入 Core 中同步执行。`job wait --for 10s` 最多观察
十秒，可能返回仍为 pending/running 的记录；停止观察不取消 Job。`job abort` 请求 best-effort 停止，
`abort_requested: true` 不代表执行端已经完成清理。只有后续记录才能说明实际结果。

`connection` 只管理本机连接，`config` 管理远端 deployment config；Agent definition 由 `agent` 管理，
模型目录由 `ai` 发现。Extension 配置和运行目标使用 `extension`，不混入 connection。

Extension management v2 uses `extension select COORDINATE --distribution python --version VERSION`
to select an exact version for the connected Peer, then `extension enable COORDINATE` to enable it.
Both commands accept `--peer PEER_ID` to target another Peer. A Web Peer's Distribution is
`module_federation`; it cannot run as a Python package on Core. Disable the Extension before
`extension unselect COORDINATE` to remove that Peer's selection. Shared configuration and collected
information remain. `extension status [COORDINATE]` reads the connected Peer's selection and runtime
state; `extension get` reads shared identity and configuration. Configuration `--schema` also comes
from the connected Peer, rather than the shared Extension row. These commands replace
`install/uninstall` and require the current Core v2 API, with no automatic fallback.

## Agent Query

Agent Query Sink 用 AI 组合已有 info-base 检索、Resolver 读取和图导航，并通过异步 Job 交付答案。先从
`ai models` 取得模型 ID，再采用
[推荐 Agent definition](../docs/30-unit-tdd/agent-query-sink.md#definition-and-result-contract) 创建 Agent：

```sh
inkcre-cli agent create --input agent-query-agent.json --json
inkcre-cli sink create --type core.agent-query.v1 --schema
inkcre-cli sink create --type core.agent-query.v1 \
  --input-json '{"nickname":"query","config":{"agent":1}}' --json
inkcre-cli sink enable 1 --json
inkcre-cli query --sink 1 --schema
inkcre-cli query --sink 1 '哪些材料讨论了 Agent 工具边界？' --timeout-seconds 300 --json
inkcre-cli job wait 1 --for 10s --json
```

示例 ID 必须替换为前一步回执中的实际值。`query` 的成功回执只表示 Job 已持久化；使用 `job wait` 或
`job get` 读取最终 status，并从 `state.result.answer` 和 `state.result.references` 取得回答与依据。Sink disable
会撤下该实例的动态 query route，但不取消已经领取的 Job。

## 输出与续读

结果写 stdout，命令错误写 stderr。退出码 0 表示命令成功，1 表示远端/IO/部分结果失败，2 表示本机输入
错误，130 表示观察被中断。读取一个状态为 failed 的 Job 仍是成功读取，脚本应检查 Job 的实际 status。
批量读取与混合 recall 保留各项结果，不因一项失败丢弃其它成功项。

支持分页的目录使用 `--limit`、`--cursor` 和返回的 `next_cursor`；CLI 不自动翻页。
检索仍保留各模式自身的 limit 和结果结构，混合 recall 不融合排名。

人类可读输出过长时显示摘要和完整文件路径；`--json` 不截断、不 pretty-print。
二进制自动保存为文件；显式 `--output-dir DIR` 导出的入口 JSON 使用相对文件引用，整个子目录可以搬移。
这些是同一次 HTTP 响应的本地文件，不是新下载会话，也不会再次调用 Resolver。

开发使用独立 PDM 项目：`pdm install -p cli`、`pdm run -p cli check`、`pdm build -p cli`。
The repository Release PR prepares versions and changelogs; feature PRs record release intent in
`cli/.changes/`.
