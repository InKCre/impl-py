# ChatGPT 部署集成

本目录把正式 Core 镜像与 OpenAI Secure MCP Tunnel 客户端组合到同一容器中。
它是一个特定部署配方：Core 提供普通 MCP HTTP + PAT，集成负责 ChatGPT 的连接进程。
标准 Core 镜像不包含此目录或 Tunnel；无需修改 Core、Sink、数据库或 Extension。

目前支持 Linux amd64、单个 web 实例、单条 Tunnel 与显式指定的本地 MCP Sink。
同一信息库的其他 Peer 不自动启动客户端。不创建 OpenAI Tunnel、Sink、插件或额外服务。

## Heroku 自部署

1. 按正常流程创建 MCP Sink、个人 OpenAI Tunnel 和 ChatGPT 插件，取得运行时 Key。
2. 在 Core app 的 Heroku Config Vars 中配置下列值；不要放进镜像或构建参数。
3. 在 fork 的 Actions Variables 设置 `CHATGPT_TUNNEL_ENABLED=true`。
4. 继续运行原 `Deploy self-hosted InKCre to Heroku` 工作流。每次部署都会组合当前选定的
   Core 镜像并分别检查 Core 与 ChatGPT 接入，不需要维护个人 Dockerfile。

| Heroku Config Var | 内容 |
| --- | --- |
| `INKCRE_TUNNEL_ID` | 已有的 OpenAI Tunnel ID |
| `INKCRE_TUNNEL_API_KEY` | 该 Tunnel 可用的运行时 API Key |
| `INKCRE_MCP_SINK_ID` | 本地已启用的 MCP Sink 正整数 ID，没有默认值 |
| `INKCRE_MCP_PAT` | 该 Sink 的 PAT 原值，不带 `Bearer ` |

运行配置以 Heroku 为准；CD 只读取、检查，正常更新不会覆盖它们。首次配置可先在普通
Core 上建立 Sink 后再启用集成。PAT 的真值由 Sink 配置拥有：轮换时需同步更新这里的
凭据，否则检查会报告认证失败。日志及检查输出不打印凭据。

移除 fork 变量或设为 `false` 后，下次自部署恢复标准 Core 镜像，停止运行 Tunnel；不会
删除 Sink、OpenAI Tunnel、信息或凭据。镜像回退不会改变数据库，仍须核对目标 release
是否会回退其他 Heroku 配置。不要同时运行本机同一 Tunnel 客户端。

## 构建与独立验收

标准 Core 先按自己的流程构建。此目录仅接收一个准确镜像引用（或本机已构建的 tag）：

```bash
bash deploy/integrations/chatgpt/build.sh CORE_IMAGE CHATGPT_IMAGE
pdm run python -m pytest -q deploy/integrations/chatgpt/tests
pdm run python -m deploy.integrations.chatgpt.heroku preflight --app APP_NAME
pdm run python -m deploy.integrations.chatgpt.heroku verify --app APP_NAME
```

Heroku 检查读取 `HEROKU_API_KEY`，本机未设置时使用已登录 Heroku CLI 的 token。
构建固定下载官方 Tunnel Client 0.0.19，校验固定 SHA256，保留许可证和配套文件。
发布组合镜像时记录 Core 引用、集成源码提交和镜像摘要；不将候选镜像冒充标准 Core。
标准 Core 发布本身不发布此组合；Heroku 自部署工作流是其交付入口。

## 生命周期与可用性

启动器先执行原 Core 命令，等待 localhost `/readyz` 成功，再连接
`http://127.0.0.1:$PORT/sinks/$INKCRE_MCP_SINK_ID/mcp`。Core 自身 exec 重启流程不变。
Tunnel 退出仅重试自身（10–60秒退避）；Core 退出则结束组合；SIGTERM/SIGINT 回收两个
进程组。客户端本地 `/readyz` 状态改变时输出 `Tunnel ready` / `Tunnel not ready`。

CD 验证运行命令、当前 dyno 启动后的客户端就绪记录，并用 PAT 对目标 Sink 做 MCP
initialize。无本轮就绪记录会失败，不会用旧 dyno 的记录判定成功。这证明客户端就绪与
下游认证正常，不能代替真正的 ChatGPT 授权与调用验收；首次接入仍需通过个人插件读回
一条已知数据。后续诊断可查看 Heroku web 日志；Core `/readyz` 不包含 Tunnel 状态。

同一 Eco dyno 共享 CPU 和内存，不新增 dyno、Cron 或保活。Tunnel 的普通进程故障不会
主动重启健康 Core，但内存耗尽等资源故障仍可能影响两者。Eco 休眠会同时停止连接；
需要时先从任意设备访问 Core `/readyz` 唤醒，再等待 Tunnel 连接后使用 ChatGPT。
不承诺休眠时 ChatGPT 请求能唤醒 dyno，也不承诺用尽 Eco 小时额度后可用。
