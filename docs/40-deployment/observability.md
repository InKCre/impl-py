# 可观测性记录、查询与额外 OTLP 出口

基础观测默认使用内置信息库，Job 生命周期、操作摘要及已配置级别的日志由同一 Core 入口采集。部署选择外部 Loki 后，基础记录写入和查询都使用 Loki；故障不会退回 PostgreSQL。额外 OTLP Trace/指标出口仍由本地开关控制，不是基础查询 API。共享行为由[跨 Peer 可观测性契约](../_shared/20-product-tdd/observability-contract.md)拥有。

## 基础后端与统一查询

公共选择记录为 `configs` 的 key `inkcre.observability.backend`、schema `inkcre.observability.backend.v1`，value 为 `{"backend":"postgresql"}` 或 `{"backend":"loki"}`。缺少选择记录时使用 PostgreSQL；配置读取失败或非法时不启动基础 writer，不擅自当作缺省。它独立于旧 `inkcre.observability.v1`，避免旧 Peer 的严格 schema 拒绝新字段。普通内置使用无需生成额外部署身份。

Loki 是首个双向外部实现，使用 `/loki/api/v1/push` 和 `/loki/api/v1/query_range`，不依赖供应商 SDK。服务端还需要旧身份记录中的 `deployment_id`（用于固定部署流隔离）及私密配置：

```dotenv
OBSRV__LOKI_URL=https://your-loki-host
OBSRV__LOKI_HEADERS='{"Authorization":"Basic YOUR_ENCODED_VALUE"}'
# 默认不将普通日志原文复制到外部；确需启用时先确定访问、保留与删除政策。
OBSRV__EXTERNAL_CONTENT_ENABLED=false
```

URL 不带凭据、query 或 fragment；私密 headers 不进入共享 value、浏览器或错误响应。Backend 选择是唯一存储选择源，旧 `OBSRV__LOGGING_BACKEND`/Logtail writer 配置不再选择当前存储；旧 Logtail 配置会给出迁移提示。`OBSRV__LOGGING_BACKEND_LEVEL` 继续控制普通日志级别，最低生命周期事件不依赖该阈值。切换后重启 Peer/重建客户端，历史记录留在旧后端，不自动搬迁、删除或合并查询。

`core.observability.v1` 通过已有 Peer HTTP 协议公告 `POST /observability`，复用 Peer JWT；客户端不能传上游 URL 或认证头。请求 action 为 `status`、`query`、`ingest`。外部委派携带 `expected_backend`，与提供方实例不符返回 409，防止选择改变后误写其它后端。内置消费者可直接通过数据库协议读取，不以 Python Peer 在线为前提。

查询时间窗为 `[since, until)`，最长 31 天、每次 1–500 条。所有 severity、kind、Job、Trace、Peer、operation、文本过滤在后端截断之前执行；文本按字面匹配，不是 LogQL。返回 records、truncated、时间窗及当前 backend，按时间倒序。截断时缩小时间或过滤范围；没有会跳过同刻记录的虚假 cursor。旧 PG 日志保留原 body/attributes 和 `job.<id>` 关联，未产生的历史事件不会补写。

每个 Core 实例最多四个 HTTP 能力请求、15 秒总预算；query 最多四个并发、10 秒预算。摄取最多 100 条/256 KiB，记录说明最多 8192 字符、64 项标量元数据、每项文本最多 1024 字符。状态中的 configured/queryable 代表已具备配置/能力，不是健康探测；dropped 是本实例已知损失，不是部署总数。接受摄取只表示进入队列，不保证持久化。外部错误返回固定诊断，不回显上游响应或密钥。

标准记录 kind 为 log/event/span；span 是耗时/结果操作摘要，不冒充完整 Trace 树。severity_number 继续使用 OTEL 1–24（INFO 9、WARNING 13、ERROR 17）。Job 只有领取成功才记开始，数据库实际关闭后才记终态；失败/超时及失败操作记录 ERROR，取消为 INFO。AI 用量缺失与真零分别保留。选择 Loki 不自动扩大原文权限：默认只保留声明元数据，普通日志 body 替换为固定 `application.log`；显式启用内容另受 owner 的访问和保留政策约束。

SDK 消费者可以创建 `ObservationRuntime(PostgreSQLBackend(core.sessions), selected="postgresql", peer_id=...)` 并赋给 `core.observability`，在自己的异步生命周期中显式调用 `start()`。LokiBackend 接受显式 URL、部署身份和私密 headers；传入的 HTTP client 是借用资源，库不会关闭它。`Core.aclose()` 收束自己的 writer，再关闭自建数据库；多 Core 不共享队列或目标。普通 SDK 导入不启动任何记录任务。

## 配置与启停

每个 Python Peer 在运行环境或本地 `.env` 设置 `OBSRV__TELEMETRY_ENABLED=true` 才启用。配置 endpoint、认证头或安装 SDK 都不能自动打开它。修改后重启进程；没有后台配置探测或热重载。关闭时不初始化额外 OTLP provider 或导出线程，基础后端仍按上述选择记录和查询。

部署 owner 在数据库已完成初始化后运行 `pdm run python scripts/observability.py`。该命令只在 `configs` 缺少 `inkcre.observability` 时插入一次 UUID；重复运行保留已存在的身份和配置，不能用于重置克隆后的身份。它不会启用任何 Peer。独立 preview、数据库克隆或新 owner 的部署，在启用前显式更换 deployment ID。

共享配置 schema 是 `inkcre.observability.v1`，包含 `deployment_id`，以及可选的 `otlp_http_endpoints`（`traces`、`logs`、`metrics` 完整 URL）和 `diagnostics_url`。共享 URL 禁止 userinfo、query 和 fragment。私密认证始终留在服务端运行配置，不写入共享 value、浏览器 bundle 或诊断链接。

```dotenv
OBSRV__TELEMETRY_ENABLED=true
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://your-otlp-host/otlp/v1/traces
OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=https://your-otlp-host/otlp/v1/logs
OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://your-otlp-host/otlp/v1/metrics
OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic%20YOUR_ENCODED_VALUE"
```

当前出口为标准 OTLP/HTTP protobuf。三个 endpoint 使用完整地址，不自动添加路径；未配置的信号不导出。运行环境和 `.env` 的 per-signal endpoint 优先于共享默认值。认证支持公共 `OTEL_EXPORTER_OTLP_HEADERS` 和优先级更高的 `OTEL_EXPORTER_OTLP_{TRACES,LOGS,METRICS}_HEADERS`。服务名称、版本、运行实例由本实现提供，不读取任意 `OTEL_RESOURCE_ATTRIBUTES`，避免将配置原文引入 Resource。

Python 在数据库准入、Peer 注册之后读取一次共享配置，读取限时五秒；基础 backend 选择和额外 OTLP 初始化分别报告失败，不影响业务 readiness。单信号初始化失败只报告不含原始配置的本地诊断，不改变业务 readiness 或 PG 生命周期。配置恢复后需要重启，不自动轮询重试。

Grafana Cloud Free 是当前首个部署目标。`Authorization` 的 Basic 值必须是 `base64(instance ID:token)`，不能在 `Basic%20` 后直接拼原始 `glc_` token。Cloud Portal 的 stack 页面中，使用 OpenTelemetry 的 Configure 获取基础 endpoint 和认证变量；per-signal 地址应追加各自的 `/v1/traces`、`/v1/logs`、`/v1/metrics`，Python 的 Basic 空格使用 `%20`。[Grafana 官方说明](https://grafana.com/docs/grafana-cloud/send-data/otlp/send-data-otlp/)。账号准入、实际免费额度和查询读回必须在目标 stack 单独验收；配置示例不证明云端已通过。不要为本接入启用付费计划或依赖试用专属功能。

## 浏览器转发

浏览器不能使用上述私密 Grafana 写入凭据。client-web 的本地连接配置单独控制启用；需要服务端转发时，显式配置 relay 基础 URL，例如 `https://core.example/telemetry`，由客户端复用该连接的现有短效 Peer JWT。没有 relay 时，只能使用已准入、适合公开客户端的出口。relay URL 本身不启用遥测，也不改变业务的数据库直写或 Peer 路由。

core-py 的 `POST /telemetry/v1/{traces,logs,metrics}` 复用普通 Peer JWT 认证，仅向自身已成功启用的对应信号出口发送。调用方不能指定目标或转发认证头；服务端使用本地私密认证。它仅接受标准 OTLP/HTTP protobuf，直接使用标准 protobuf 请求与响应；JSON 返回 415。上游的 200 或 204 视为接收成功，204 的空响应映射为空 protobuf 成功响应；仍须独立查询验证持久化。client-web 使用官方 protobuf exporter，避免应用自行实现 OTLP JSON 的十六进制 Trace/Span ID 规则。不增加持久队列、后台重试或任意 URL 代理。

每个请求最多 256 KiB；每个进程最多四个转发请求同时进行，上游 HTTP 使用对应信号的 timeout；请求读取和发送的总预算为该 timeout 加两秒（最多三十二秒），不跟随重定向。不支持压缩请求。关闭或目的地不可用返回 503，满额返回 429，超大返回 413，格式错误返回 400，转发失败返回固定的 502，不回显上游错误体。转发会唤醒其宿主并消耗按请求资源，因此必须由部署显式选择；它不承诺遥测零额外运行费用。

## 首批采集与查询

| 边界 | 可观察内容 |
| --- | --- |
| `http.server` / `peer.http` | 路由模板、状态、耗时；Peer 目标及未执行、结果未知或已响应，不导出 URL query、headers 或 body |
| `job.submit` / `job.execute` | Job ID、受限提交 carrier、独立执行 Trace 与 Span Link；提交写入 span 只证明事务内 flush，`job.submitted` / `job.closed` 事件在本地拥有的事务提交后发出 |
| `cron.materialize` | 每次检查的 Cron ID 和当次实际创建的 Job，不复用最初创建 Cron 的请求上下文 |
| `ai.chat` / `ai.embed` | 共用 AI 执行边界；配置的模型标识、provider/dialect、耗时、真实 usage 和受控结束原因 |
| `agent.turn` / `agent.step` / `agent.tool` | Thread、turn、步骤、并发父子关系与工具结果；动态业务 ID 不进入指标维度 |
| Agent 检索、读取与结果 | 分开记录候选实体、实际读到的实体、Resolver 成功读取及最后选中的引用；只导出受控 ID 和计数 |

`inkcre.operation.count` 和 `inkcre.operation.duration` 记录操作范围的 `success`、`error`、`cancelled`，结果独立于是否开启 Trace。它们不是全库 Job 终态统计；过期收敛等未进入执行 scope 的路径不能从这个 counter 推算。请求 5xx、已捕获的 Job 失败/取消、工具错误结果在业务边界显式分类。耗时直方图按秒显式分桶，从 5ms 到 300s，覆盖 HTTP 短调用与较长 AI/Job 操作。指标只使用固定操作和结果等有界标签，不包含 Job、Trace、Thread、Peer 实例或动态模型 ID。

资源使用 `service.name`、`service.version`、`service.instance.id`、已知的 `inkcre.deployment.id` 与 `inkcre.peer.id`。查询单个任务用 `inkcre.job.id`，从执行 Trace 的 Link 查提交 Trace；同步 Peer 调用使用标准 W3C context。只有配置的 Peer 传输注入上下文，外部模型请求不注入内部 Trace Context。关闭的提交方写 NULL，关闭的执行方不创建 Trace，但领取和关闭保留已有 carrier。缺失、采样和过期导致的空缺不是业务未执行的证据。

生成的 Job carrier 各最多 512 UTF-8 bytes；SDK 注入的可选 tracestate 超限时整体省略并记录固定原因事件。SQL 容量约束拒绝直接超限输入。正式 migration `3d9593b0c855` 增加两列；nullable 不解除 exact-head 准入，部署需协调更新 schema、Python 与客户端，刷新 PostgREST schema cache。关闭遥测无需 downgrade 或删除任何 Job、PG 日志或 carrier。

## AI 数据边界

GenAI 映射固定到 `semantic-conventions-genai` 修订 `e07f4ebacb08f56db8c4c882d117720333fbca04`，由 AI owner 的 `telemetry.py` 维护。OpenAI-compatible 和 Alibaba adapter 在真实 SDK 回包处采集；Alibaba 的空 choices 尾块仍读取 usage，重复累计总量只记最后一次。input/output 缺失分别标记 `unavailable`，真实非负 int64（包括零）标记 `provider`。缓存和推理 token 不重复加到 input/output 总量。

`inkcre.ai.token.usage` 只累计已知值；缺失、负值、布尔与越界值不添加零或负样本，缺失另记 `inkcre.ai.usage.missing`。当前没有价格配置或成本估算，未返回的成本保持未知；Token 总和及采样 Trace 都不能冒充 provider 账单。

基础模式不导出 prompt、答案、工具输入输出、query、provider 配置、任意异常消息或 stack。配置中的模型标识受字符集与长度限制，provider 任意回显 ID/model 不复制；ToolCall ID 使用 SHA-256 摘要关联。最终引用最多导出前 128 项中的有效 ID，同时记录总数和未导出数量；不截断原业务结果。

首批来源事件分别是 `inkcre.retrieval.candidates`、`inkcre.entity.read` 和 `inkcre.agent.query.result`。最后一个事件描述选入 Job 内存 state 的结果，不提前声称最终数据库 close 已成功。当前点位覆盖 Agent 的 retrieve/get_entities/Resolver Tool 与 Agent Query Sink；直接非 Agent 检索、图遍历和 Resolver 内部传递读取尚无完整来源事件覆盖。关联不能证明模型确实依据该实体或答案正确，也不提供长期内容快照。原有 `agent_debug` 及历史存储可能包含内容，不能将新外部出口的元数据策略泛称为整个部署没有原文副本。

## 故障、关闭与迁移后端

需要队列丢弃及导出观测值时，在进程环境设置 `OTEL_PYTHON_SDK_INTERNAL_METRICS_ENABLED=true`；Compose 默认值为 true，允许显式覆盖。原生启动用 `export` 或 dotenv 启动器注入，单由应用 Pydantic 读取 `.env` 不会改变 SDK 读取的进程环境。此变量不自动启用遥测。配置 metrics 出口后，原生 SDK 内部指标只保留组件类型、错误类型与 HTTP 状态；不导出 endpoint、组件随机名称。未配置 metrics 或显式关闭内部指标时只有本地固定 warning，没有 SDK 丢弃计数。SDK 队列并发检查可使计数与真正损失存在误差，指标本身也可能丢失；它们用于诊断，不是精确损失账本，缺少样本不能推断零丢弃。

Span 和 log 各使用 SDK 的 256 条有界队列、最多 256 条一批、五秒周期；metric 为三十秒 push，不增加 scrape 或唤醒归零实例的采集轮询。HTTP 导出 timeout 采用标准 `OTEL_EXPORTER_OTLP_TIMEOUT`，缺省十秒；按信号的 `OTEL_EXPORTER_OTLP_{TRACES,LOGS,METRICS}_TIMEOUT` 优先，允许有限正数且不超过三十秒。真实 Cloud 首次 TLS 往返超过一秒，固定一秒会误丢弃可用出口的数据；故障实验使用显式一秒配置。源端 SDK warning/导出失败只输出固定本地诊断，不转发 SDK 错误体或凭据。

正常关闭先完成业务资源，并发关闭额外 trace/log SDK，最后关闭 metrics；Core 随后停止接受基础记录，最多五秒排空有界队列、两秒关闭后端，再释放数据库。SDK 1.45 的 trace/log worker 等待窗口为三十秒，metrics shutdown 等待参数为对应信号 timeout 加两秒（缺省十二秒）；这些不是网络总时限或交付保证。不先执行一次会忽略 timeout 的 force_flush。平台提前杀进程、请求后冻结或断网时允许丢失尾部记录；使用该平台前需要验证实际终止宽限期，不能延长每个业务请求来掩盖此限制。

更换后端只调整标准出口和服务端认证，重新验证 ID、Links、AI unknown/zero 与聚合；Grafana 的查询、datasource、面板及诊断链接留在部署/消费侧。历史数据、查询和面板不承诺无成本迁移。关闭本地 OTLP 开关只关闭额外出口，不改变基础存储。基础存储切回 PostgreSQL 必须显式修改公共选择并重建实例，不能把外部读取失败当作空结果；历史记录不自动迁移。

查询验收使用独立 Viewer service account，经 Grafana 的 datasource query/proxy API 读取 Tempo、Loki、Prometheus；`GRAFANA_URL` 与 `GRAFANA_SERVICE_ACCOUNT_TOKEN` 仅供部署工具使用，不进入应用采集。写入成功必须再核对存储后的 ID、Links、AI 来源/未知值与指标。若本机 Python 没有默认 CA 路径，在进程环境用标准 `OTEL_EXPORTER_OTLP_CERTIFICATE` 指向可信 CA 文件，不能关闭 TLS 校验。
