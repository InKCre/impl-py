---
description: Twitter Python 分发的宿主要求、公共地址、授权配置与采集参数。
---

# Core 运行与配置

Python 分发在所选 Core 上提供 x-monkey 归档协议、Bookmark Source 及 Twitter Resolver。它不包含 Web
Setup 或阅读界面；用户工作流由本扩展的 **global** 文档说明，Web 另选兼容的 Module Federation 分发。

Python 分发要求 Python `>=3.12,<3.13` 和 Core Host SDK
`>=0.3.1 <0.4.0`。选择精确分发、启用意图和实际运行属于相应 Peer；扩展配置与账号状态由部署共享。

## 公共地址与生命周期

在执行该能力的 Core Peer 配置
`http_public_base_url`，使用浏览器或脚本实际可访问的 HTTPS 基址。Setup 由此产生
`/twitter/auth/callback` 和 `/twitter/x-monkey`
地址，保留部署路径前缀。不要使用 PostgREST 地址代替 Core 地址，也不要把预览环境地址作为其他部署的默认值。

启用 Python 分发后才会注册 Source、Resolver 和 HTTP 路由；停用撤回入口，不删除已保存的图信息。Web 独立启用不会让离线 Core 执行采集，安装状态也不等于实际运行状态。多 Peer 可以具备采集能力，但同一 Source 不应配置重复的采集计划。

## 扩展配置

下表是 `TwitterExtensionConfig`
的实际字段，通过既有扩展配置管理接口修改。OAuth 向导会同时维护相应账号状态，优先使用向导配置官方后端。

| 字段                            | 默认值     | 用途                                                            |
| ------------------------------- | ---------- | --------------------------------------------------------------- |
| `backend`                       | `official` | `official` 使用 X OAuth 用户授权；`twikit` 使用账号登录。       |
| `client_id`、`client_secret`    | 空字符串   | 官方 OAuth 应用的 Client ID/Secret，不是 API Key/Secret。       |
| `email`、`username`、`password` | 空字符串   | `twikit` 登录所需账号信息。                                     |
| `totp_secret`                   | `null`     | `twikit` 可选 TOTP 秘密。                                       |
| `api_language`                  | `en-US`    | 传给 `twikit` 客户端的请求语言。                                |
| `proxy`                         | `null`     | 传给 `twikit` 客户端的代理；当前官方 OAuth 客户端不读取此字段。 |

保存 OAuth 应用会把 `backend` 设为 `official`。如果应用变更会影响已有账号或待完成授权，setup
API 要求显式
`confirm_account_reset`；配置指纹改变会清除不再匹配的账号和授权事务。不要盲目确认重置或直接编辑持久 state 来绕过它。

官方后端把账号 token、user
ID、handle 和续期状态保存在扩展 state；账号授权属于整个扩展，不属于某个 Source。官方客户端使用当前授权刷新 X
token，供应商拒绝授权时标记需要重新连接。凭据及授权链接不应进入日志或截图。

`twikit` 在实际采集时登录，使用 `data/extensions/twitter/twikit_cookies.json`
作为运行实例的 cookie 文件。该路径需要可写并保护其中的会话数据；它不是共享 info-base 的账号状态，也不会自动在多个 Core 之间同步。OAuth 向导不能代替这套账号登录配置。改变后端或账号后应在正常生命周期中停用并重新启用，以结束旧的本地客户端。

## setup API 与认证边界

运行人员或 Agent 可通过既有 Peer HTTP 方式调用下列 Core 路由。setup API 要求 Peer JWT
Bearer；CLI 当前没有专用 OAuth 设置命令。

| 方法与路径                               | 作用                                                                    |
| ---------------------------------------- | ----------------------------------------------------------------------- |
| `GET /twitter/setup`                     | 读取 callback URL、可用 x-monkey 基址和账号连接状态。                   |
| `PUT /twitter/setup/oauth-app`           | 提交 `client_id`、`client_secret`，必要时明确 `confirm_account_reset`。 |
| `POST /twitter/setup/oauth-transactions` | 创建十分钟有效的授权事务，返回供用户打开的私有授权链接。                |
| `POST /twitter/setup/oauth-transaction`  | 以 `transaction_id` 读取该事务结果。                                    |
| `DELETE /twitter/setup/account`          | 断开扩展当前连接的 X 账号，不删除已采集信息。                           |

用户仍需在 X 页面批准正确账号，浏览器回调为
`/twitter/auth/callback`。新建授权会替代此前未完成的尝试；替换应用或账号会影响所有 Bookmark
Sources。

x-monkey 的
`/twitter/x-monkey/api/v1/session`、`/twitter/x-monkey/api/v1/archive/query`、`/twitter/x-monkey/api/v1/archive/submit`
使用协议专用 Cookie，与以上 Bearer 路由分开。首次连接由 Web 向 `/twitter/x-monkey/session`
顶层 POST 新鲜 Peer JWT；目标由浏览器 Host/Auth 根据所选 Peer 的 live
lease、`inkcre.twitter.x-monkey.session.v1` 能力广告和该 Peer 公共基址及路径前缀派生，Twitter
UI 只提交连接意图。Core 为相同完整 Peer 权限签发带 `session_use=twitter_archive`
的会话 JWT，Cookie 路径不构成降权。Cookie 为 Secure、HttpOnly、SameSite=None，要求正式链路使用 HTTPS；普通 Core 保护路由不读取它。

Python Twitter 只在 session 路由就绪且配置 HTTPS 公共基址时广告该能力，协议为
`core.peer.protocol.browser-form.v1`，参数为
`{"method":"POST","path":"/twitter/x-monkey/session","credential_field":"token"}`。停用随路由撤回广告；setup 响应中的服务地址仍供用户复制，连接按钮独立取决于所选 Peer 的能力广告，不能用展示地址代替 Host 的凭据投递目标校验。发起导航只返回
`initiated`，会话是否建立仍由账户页及脚本检测确认。此交接需要 Web Host SDK
`@inkcre/core >=0.4.1 <0.5.0`，Python Host 最低范围保持不变。

## x-monkey 寿命配置

`TWITTER_X_MONKEY_JWT_LIFETIME_SECONDS` 是 Core 环境配置，不是扩展 JSON 配置字段。默认 `604800`
秒，即 7 天，只接受正整数。首次连接与续签使用同一配置；有效协议请求在剩余期限不超过当前配置寿命的一半时续签。

此值是以后签发 token 的默认期限，不是 JWT 准入上限；降低它不会撤销仍有效的既有 token。它与普通 Core 签发使用的
`JWT_DEFAULT_LIFETIME_SECONDS` 分开。没有额外 PAT、refresh
token、会话表或从首次授权计算的绝对上限。断开只清理当前浏览器 Cookie，不能撤销复制的 JWT；部署更换原有 JWT_SECRET 后按既有机制失效。

## Bookmark Source 与采集参数

Source 类型为 `extensions.twitter.bookmark.Source`，Source
config 当前为空对象。普通采集通过既有 Source Job 执行，参数如下：

| 参数           | 默认值  | 实际行为                                                                                                       |
| -------------- | ------- | -------------------------------------------------------------------------------------------------------------- |
| `result_limit` | `40`    | 整数，5–100，作为本次一页请求的结果上限。                                                                      |
| `full`         | `false` | `false` 时按 `latest_tweet_id` 截断本页旧书签并更新主书签游标；`true` 时不截断，并将下一页位置记到 Job state。 |

每次 Job 只请求一页，不自动提交后续 Job；`full`
不等于完整回填。此 Source 没有独立 backfill 实现。分页位置和 Source 游标是运行状态，不应当成账号配置手工重置来排查授权故障。外部请求失败与数据库写入失败不能被报告为成功采集。

媒体只保存原生元数据，Python 写入不创建持久图片/视频内容 Block、媒体 blob 或下载 Job。只有消费端明确展开时才通过现有 HTTP
Storage 读取源站字节。词法索引维护独立执行，不在 submit 或 collect 中隐式完成。

持久内容由 Twitter 自有共同模型拥有，x-monkey 固定 DTO 在适配边界转换。官方 API 和 Twikit 只投影当前支持的字段，不保存全部原始响应。消费端选择图片或最高码率的可用 MP4 后，由通用 HTTP
Storage 负责 HTTP(S)、超时、体积与重定向；Twitter 不另设 CDN 或固定端口限制。
