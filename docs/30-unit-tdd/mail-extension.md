# Mail Extension

## Purpose

本文记录 core-py `mail` extension 的稳定本地架构。Mail 保留完整 email client/agent 的长期方向；当前实现边界是
可信的 IMAP collection baseline、canonical Mail graph、read-only solved projection，以及显式 MIME-part
materialization。它不提供 Mail 专用浏览页、compose/send/draft 或后台 IMAP IDLE。

```text
manual/Cron-created typed Job
  -> Mail Source
  -> protocol-neutral Mail adapter
  -> source-owned reconciliation and graph effects
  -> Mail Resolvers
  -> generic InfoBase use

Mail MIME Resolver
  -> same adapter family
  -> writable Storage
  -> semantic content Block + content Relation
```

Hub Product TDD 只拥有 generic Source、Job/Cron、graph、Storage、Resolver、Peer 与 InfoBase route contracts；本文拥有
完整的 Mail-specific product/technical contract，包括 Python package topology、exact IDs、Mail schemas、IMAP
checkpoint policy、graph grammar 与 acceptance boundary。

## Package Topology

```text
extensions/mail/
  __init__.py       extension lifecycle、config defaults、exact Peer inbound
  schema.py         commands、canonical facts、checkpoints、solved projections
  adapter.py        protocol-neutral port、IMAPClient adapter、factory
  source.py         ordinary/backfill Source policy and state advancement
  reconcile.py      reconciliation ladder and transactional graph effects
  resolver.py       read projections and remote MIME materialization
  newsletter.py     offline newsletter body projections and exact body resolver IDs
```

`MailProtocol = Literal["imap"]` 是公开协议选择，不是 InKCre adapter ID。`create_mail_adapter(protocol, parameters)`
是唯一 shallow construction seam；每个 Source command 与 Resolver materialization command 使用 fresh async-context
adapter。Adapter 产出 canonical remote facts/checkpoint proposals，不生产 Block/Relation、不持久 state，也不拥有
collection 语义。

## Exact Runtime IDs

- Source type：`extensions.mail.source.Source`；
- Resolvers：
  - `extensions.mail.email.v1`；
  - `extensions.mail.mailbox.v1`；
  - `extensions.mail.email_address.v1`；
  - `extensions.mail.flag.v1`；
  - `extensions.mail.mime_part.v1`；
  - `extensions.mail.mailing_list.v1`；
  - `extensions.mail.newsletter_html.v1`；
  - `extensions.mail.newsletter_text.v1`；
- remote materialization capability：`extensions.mail.mime_part.materialize.v1`；
- provider route：`POST /mail/mime-parts/materialize`。

Extension config 只拥有新 Source 可继承的 default mailbox exclusion policy。Source 第一次使用时把 null policy
materialize 为自己的 snapshot；后续 extension config 变化不 retroactively mutate existing Sources。

## Source Config And State

Source config separates protocol identity from parameters：

```text
protocol: "imap"
parameters: { host, port, security, username, password }
excluded_mailboxes: { names, special_uses } | null
ordinary_mark_as_seen: boolean
backfill_mark_as_seen: boolean
synchronize_deletions: boolean
```

Source state 只保存 non-secret access binding 与 per-Mailbox ordinary checkpoints。Binding 改变不能静默接续旧
state/graph effects。Checkpoint 由 adapter 解释，Source 决定 accepted-effect boundary 与持久推进时机。

Ordinary collection 默认只接纳 Source creation time 之后的信息；第一次建立 checkpoint 后使用：

1. QRESYNC（可靠 VANISHED + changed flags）；
2. CONDSTORE（changed/new occurrences，无删除证明）；
3. new-occurrence-only。

不以 full mailbox scan 猜 deletion。Backfill 是独立 exact Job type，使用 `[since, before)` date interval，不读取或
写入 ordinary checkpoints，也不被代码禁止进入 Cron。

## Canonical Graph

```text
Source anchor --manages--> Mailbox
Mailbox --contains {uid_validity, uid}--> Email
Email --{role:"body", part_id}--> core.text.v1 / core.html.v1
MailingList --publishes--> Email
Email --{role:"body", part_id, alternative_groups}--> newsletter_text.v1 / newsletter_html.v1
Email --{role:"inline|attachment", part_id}--> MIME-part metadata
Email --{role, order, display_name}--> EmailAddress
MailFlag --tags--> Email
reply Email --parent:<order> / reference:<order>--> target Email
HTML body --{type:"embeds", reference}--> MIME-part metadata
MIME-part metadata --content--> semantic content Block
```

Email root 只保存 `{message_id,email_id,subject,authored_at}`。Mailbox root 保存
`{name,special_uses,mailbox_id}`。MIME root 保存 source-declared semantic metadata；`part_id` 属于 Email component
Relation，因为它同时表达 MIME-tree location/order 与 exact remote fetch locator。Participants、membership、flags、
references、attachments 与 materialized bytes 不复制进 Email content。

Source anchor 是 Source authority 的 lazy graph projection；`sources.block` nullable unique，projection content 只为
use-time label/text 与 historical readability 服务。

## Identity And Reconciliation

Email reconciliation 是 linear exact ladder：

1. same-Source Mailbox + UIDVALIDITY + UID；
2. comparable cross-Source exact occurrence；
3. scoped EMAILID；
4. Message-ID；
5. create。

每一 rung 都遵守 `zero -> continue / one -> reuse / many -> stop-and-create`。Null identity 可以被后续 exact fact
补全；non-null contradiction 拒绝 reconciliation。弱 Message-ID match 涉及 MIME remote locator 时 lazy-duplicate，
不根据 metadata 猜 UID/part。

Mailbox 永远 Source-scoped，不跨 Source merge。同一 Mailbox/canonical Email pair 至多保留一个 live `contains`；
同 Mailbox duplicate occurrence 创建另一个 Email Block。Plain `tags` 是 canonical best-effort flag projection；完整
FLAGS response 替换当前 observed set。可靠 removal 只删除 exact membership 与相关 tag effects，不删除 canonical
Email graph。

## Newsletter 阅读与刊物归档

Adapter 优先使用邮件中单个、格式有效的 `List-ID` 提取刊物声明。`list_id` 按
[RFC 2919](https://www.rfc-editor.org/rfc/rfc2919) 规范化大小写，显示名称独立保存；
发件地址、主题或 `List-Unsubscribe` 不能代替刊物身份。`List-ID` 表示邮件声明的列表归属，
不是发件人认证，也不能区分编辑型 newsletter 与讨论型邮件列表。

缺少 `List-ID` 时，仅支持 Beehiiv 的显式刊物声明：单个 `X-Beehiiv-Type: newsletter`
和单个 `X-Newsletter-ID`。后者必须是无凭据、查询参数及片段的 HTTP(S) 刊物 URL，
以 `beehiiv:<规范化 URL>` 保存到现有 `list_id` 字段。规范化只处理协议、域名、默认端口和空路径，
保留路径大小写；域名用作显示名称。重复或无效的 `List-ID` 不会触发该分支。
两类声明都不代表身份认证；如果供应商后来改用 `List-ID`，系统不会猜测两个身份相等并自动合并归档。

Reconciler 通过 `extensions.mail.mailing_list.v1` 的 `list_id` 查找刊物 Block，
不存在时创建，存在时稳定复用 ID 最小的节点，并建立幂等 `publishes` 关系。
并发首次采集可能留下多个同身份节点，归档按相同 `list_id` 汇集各节点的关系；
后续采集和重放不会继续制造刊物节点，也不删除已有关系。
刊物分组不参与 Email 身份 reconciliation，不改变 UID 或附件定位。网页端从邮件进入刊物归档，
按日期展示各期；仅把同刊物内非空 Message-ID、主题、发送时间均相同的 Email 折叠显示。
每个副本仍保有独立入口；相同主题但 Message-ID 不同的期数保持分开。折叠是阅读行为，不删除或合并邮件数据。

已识别刊物的 text/plain 与 text/html body 分别使用 `newsletter_text.v1` 和
`newsletter_html.v1`；内容仍为未经改写的正文字符串。Resolver 生成清理后的 solved content，
其文本投影供默认读取和 lexical 索引使用。HTML 清理移除显式隐藏内容、跟踪像素以及标签明确的
退订、偏好设置、浏览器阅读链接。对于链接文字只有 “here” 的邮件，清理器也识别整段文字完全匹配
已知订阅操作的段落；不删除这些链接所在的整张表格或包含其他文章文字的段落。
已知 `utm_*`、`fbclid`、`gclid`、`mc_cid`、`mc_eid` 查询参数被移除，其余参数保留原始编码和顺序。
带已知签名参数的链接整体保留，避免清理查询参数使签名失效。
无法本地解释的跳转链接保持原样，不为展开链接而请求外网。纯文本只移除明确操作行。

正文 Relation 的 `alternative_groups` 保存 MIME multipart/alternative 的全部祖先分组。
阅读与 Email 默认文本投影逐层优先选择含 HTML 的分支，保留所选分支内全部独立正文片段。
空数组表示已知没有 alternatives；null 或缺失表示旧记录未保留 MIME 树，网页沿用原来的首个 HTML 优先视图。
Email 的 lexical 投影继续只含邮件自身标题，正文子 Block 负责正文索引。
HTML 阅读和原文视图都经过同样的安全隔离；原文切换不会允许被动远程图片加载。
Newsletter 阅读模式将显式 `role="none"` / `role="presentation"` 的布局表格改为随容器宽度排版，
保留嵌套数据表的尺寸、跨行及跨列信息，并为 H4–H6 设置可读字号。
被阻止加载的外图显示替代文字；图片独占的有效链接显示“查看图片链接”，避免链接随图片隐藏而丢失。
精确匹配社交分享操作的图片链接会被清理。原文视图与普通邮件不应用这组阅读排版处理。

Web 的 newsletter 阅读以桌面为先：宽容器显示常驻文章目录，正文自动展开到全文高度，
由宿主统一滚动；目录支持标题定位和前后导航。浏览器本地记录正文 Block 的阅读位置，
重新打开提供继续阅读，内容更新时间改变后不恢复旧位置。窄容器折叠目录。
清理后的阅读 iframe 允许父页面同源读取标题和尺寸，sandbox 和 CSP 均禁止邮件脚本；
原文视图不开放同源访问，外部图片继续禁止被动加载。

Web 的列表和图把同一 Email 的 newsletter 正文命中呈现为一个阅读入口，显示刊物和日期；
详情保留全部原始 Block、摘要和匹配证据。正文必须具有唯一直接 Email body 归属且 Email 属于刊物，
否则保留原节点。该规则不改变 Python 的逐 Block 检索响应，也不把子正文复制到 Email 索引。
不同 Email 副本仅在刊物归档中折叠，搜索仍按各自 Block 身份展示。

Newsletter HTML 的 `lexical` 投影提取清理后 HTML 的可见文字，不生成表格 Markdown，
也不把 href/src 属性中的跳转地址追加到正文。作者在正文中直接写出的 URL 和代码保留；
`default` 投影及阅读 HTML 仍保留有效链接。Python 与 TypeScript 遵守同一边界。
升级投影后需要显式 lexical rebuild；仅运行 maintain 不会重建正文时间戳未变的旧索引。

旧 `core.text.v1` / `core.html.v1` 正文继续可读。升级不会自动扫描历史邮箱或改写历史记录；
显式 backfill 再次读到带受支持刊物声明的历史邮件时，会原位升级对应 body 的 Resolver 并补齐刊物关系，
保留正文 Block ID 和原始内容。Python 与 TypeScript Mail Distribution 均需支持新增 Resolver ID 后，
才能消费新产生的 newsletter graph。不存在单独的 newsletter 订阅状态或自动退订功能。

## MIME Materialization

MIME materialization 的 locator 与授权规则对普通邮件和 newsletter 一致。

Collection 保存 body content 与 MIME metadata，但不默认下载 attachment/inline bytes。`MailMimePartResolver` 先通过
singular `content` relation 返回任意 existing child；只有缺失且 `materialize_missing=true` 才：

1. 从 MIME part → owning Email → exact Mailbox occurrence → Source anchor 导出 remote locator；
2. 验证 live Source binding；
3. 通过 fresh adapter 读取 exact `part_id` 并 decode transfer encoding；
4. 以 declared media type → byte signature → `core.file.v1` 选择 core Resolver；
5. lock/recheck 后使用 Source effective writable Storage 写 bytes，并提交 child Block + single `content` Relation。

Effective Storage 顺序是 `sources.storage` → deployment `core.source` default → built-in PostgreSQL binary `-4`。
Catalog constraint 保证 explicit Source Storage 类型可写；runtime error 仍可以表达具体 instance unavailable。

client-web 无 IMAP socket，使用 exact Peer capability 请求 provider-local materialization，再从共享数据库/Storage
自行 solve returned child。Provider inbound 调 non-delegating local Resolver path；不建立 generic Resolver delegation。

## Failure And Concurrency Boundary

MailGraphReconciler 通过必需的 SourceUnitOfWork 组合 graph 与 Source 操作，不持有 raw session。
Source 入口决定逐 Mailbox/occurrence 的异步事务；网络获取和 mark-as-seen 在数据库作用域外执行。

- Job 是 one-shot envelope，无 retry/attempt；Source 可以逐 Mailbox/occurrence提交 accepted partial graph。
- Ordinary checkpoint 只在 owning accepted boundary 推进；中断后允许重扫并依赖 exact reconciliation。
- Mark-as-seen 发生在 graph commit 后，ordinary/backfill 分别配置。
- MIME materialization 先读 existing child；并发 lock/recheck 尽量避免 duplicate。即使 race 留下多个有效 child，
  solved projection 通过 singular graph read 取一个，Organization 可在以后处理 redundancy。
- Resolver public completion 不暴露 created/reused/race mechanics。Remote unavailable 是 capability failure，不通过
  UID/metadata guessing 降级。

## Verification Authority

阻塞验收使用 repository-owned professional-reading `.eml` corpus、checksum-verified Dovecot 2.4、disposable
PostgreSQL，以及 built client-web/Mail remote：

- J1：ordinary horizon、mailbox exclusions、QRESYNC flags/removals、idempotence；
- J2：exact backfill、ordinary checkpoint independence；
- J3：read-only solve、explicit image materialization、PostgreSQL bytes/local re-solve、unavailable remote outcome；
- J4：GraphSurface/InfoBaseRouter、popup literal back、Mail rendering、HTML isolation/no passive fetch、CID refresh 与 missing
  Block state。

低层 schema/helper tests 不是 Mail 产品有效性的 authority；static checks 保证 mechanical contracts，真实纵向旅程
证明 feature behavior。

Newsletter 回归另覆盖 `tests/extensions/mail/test_newsletter.py` 的协议解析和无损投影、
`test_newsletter_graph.py` 的 disposable PostgreSQL 归档/回放/历史升级，以及 TypeScript
`extensions/mail/tests` 的实际浏览器阅读、原文切换、刊物导航和副本折叠。
浏览器用内存数据替代持久层，不能代替 J1–J4 的真实 IMAP 全链路验收。
在 `impl-ts` 执行 `pnpm exec playwright test --config extensions/mail/tests/playwright.config.ts`
运行 newsletter 浏览器旅程。
