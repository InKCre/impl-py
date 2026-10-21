# CalDAV 第一方扩展

当前阶段：2026-10-21 用户明确「开始处理」后，按免人工阶段核对的 staged-collaboration 完成共享语义图实现，正在准备升级 preview 和补充真机、Web 验收。Python PR #146 与 Web PR #142 保持未合并；此前原范围验收记录保留为历史，不能替代本轮图语义证据。

已采用并实现的模型：稳定联系人／事件系列／任务根保存 `href + remainder`，只保留尚未提升的原生属性；电话、邮箱、URL、备注、描述成为 `core.text.v1`，内嵌媒体成为既有语义 resolver + PostgreSQL blob，RELATED-TO 唯一匹配同集合资源时直接指向根。`caldav:property:` Relation 保存组件、例外、提醒、角色、参数和顺序。Python 与 Web 从当前图组合内容，不另存完整可写副本。

身份和删除：当前根中相同值重排／改标签保留目标 ID；源值改变时新建目标并重新关联，不覆写可能被他处引用的旧内容。不做跨对象模糊去重。普通 Peer 编辑共享目标会影响所有引用和 DAV ETag。DAV DELETE 撤销集合成员发布，保留根、内容及外部引用；集合删除标记未发布。v1 在扩展启动时原子迁移，根 ID 不变，失败全部回滚；旧版单独运行不再支持新图。

并发：写事务依次锁定根、关系、语义目标和 blob，数据库 blob 锁接口要求 Host SDK 0.3.3；旧 ETag 写入拒绝覆盖。读事务使用一致快照，ETag 来自实际组合文本。当前仍扫描全部 DAV 图，含未发布资源，不实现自动垃圾回收。

本地证据：14 项 DAV 检查通过，包含电话重排、图片通用 resolver、直接 Peer 内容编辑、旧 ETag 412、电话和 blob 锁竞争、子任务先于父任务、撤销发布保留引用、迁移失败回滚；Python/Web 共用保真 fixture 覆盖时区、例外、未知参数与二进制提醒附件。Web 13 项测试、全工作区格式／lint／类型／构建通过。Python foundation、锁文件、Ruff、Pyrefly、数据库边界、发布契约通过。独立顾问发现的 iCalendar 附件双重 Base64 编码已修复并加入原始 bytes 检查。

后续：保留现有 preview 资源快照，升级 Python 分发到 0.2.0（Web 仍由 Changeset 决定正式版本），核实 ID／引用／协议内容迁移；直接修改电话与媒体并同步华为，再验证 Web 关联内容跳转，补齐截图教程与最终提交检查。preview PostgREST 首次读取返回 503，正在查明运行状态；未执行数据重置。

用户目标：新增第一方 calDAV 插件，让 InKCre 提供日历、任务和联系人服务；以本机连接的安卓手机上的 DAVx⁵、Tasks.org 验收。不操作现有个人数据。用户随后授权提交、推送、创建 PR，并要求完成 preview 环境验收、保存教程截图。

## 当前事实

- 第一方扩展位于 `extensions/`，采用独立 wheel 和 `inkcre.core.extensions` entry point。Memos 已提供外部协议认证、动态配置、路由启停和图持久化的参考实现。
- 共享知识契约要求 blocks + relations 为信息权威，不能增加独立 DAV 对象库作为平行业务权威。
- 2026-10-20 ADB 检出两台设备：小米 24069RA21C（b45097d7）和华为 JKM-AL00a（HYJ4C19524006587）。用户明确指定华为为验收设备，小米不用于验收。小米安装 DAVx⁵ 4.5.14-ose、Tasks.org 15.3；华为当前包列表未检出这两个应用包，验收前需核实并准备应用。
- 仓库尚未检出 CalDAV/CardDAV 实现。日历和任务分别使用 VEVENT、VTODO，联系人需要 CardDAV/vCard。
- Tasks.org 官方文档说明，经 DAVx⁵ 同步时，Tasks.org 不能创建、重命名、配色或删除远程任务列表；这些集合操作需通过 DAVx⁵ 或服务端完成。

## 已确认产品范围

一个 `inkcre/caldav` 扩展同时提供 CalDAV 与 CardDAV。按现有部署共享信任边界提供协议凭据，不引入新的多用户权限体系。允许多个日历、任务列表和通讯录，提供默认集合。

手机日历支持事件创建、修改、删除、全天事件、时区、重复及单次例外、提醒。Tasks.org 支持任务创建、修改、完成、恢复、删除、到期、提醒、重复和子任务；保留客户端额外字段。系统联系人支持姓名、多电话、多邮箱、地址、备注及照片的往返保存。协议写入和 InKCre 信息读取使用同一事实来源；停用扩展不删除信息。

并发修改必须有明确冲突响应，不静默覆盖旧版本。离线修改重连后可同步；删除与重复同步不得复活或复制对象。服务端不承担会议邀请邮件投递；不新增网页日历界面、第三方账号导入或多租户权限。

较小备选是先交付日历和任务，再加入联系人；默认不采用，因为用户明确包含联系人。

## 验收目标与下一步

使用独立测试账号／集合和显式标识的测试对象，不修改手机原有账号的数据。三类对象均验证手机写入→服务端持久化→客户端重新获取，以及服务端更新→手机同步。验证离线重连、重复同步、冲突、重启持久化、停用／启用，以及重复事件和任务完成状态。真机证据不能用模拟 HTTP 成功替代。

用户已确认范围，要求独立 worktree，并明确本任务遵循 staged-collaboration 但省去人工阶段核对；技术方案、验收设计、实施准备、实现和验证均继续自主推进。新增的推送与 PR 授权包含必要提交，不包含合并或生产发布。

工作目录：`/Users/lanzhijiang/Development/.worktrees/caldav-extension/impl-py`，分支 `feat/caldav-extension`。本 packet 为后续唯一任务状态；原 checkout 的旧副本不再更新。

## 技术方案与执行顺序

顾问独立评估后采用 Radicale 3.8.3 WSGI 协议引擎。扩展自己的 FastAPI 入口负责当前配置认证、请求大小限制和线程调度；只向引擎传入认证后的固定 `inkcre` principal。默认提供 calendar、tasks、contacts 集合。集合 Block 保存路径与属性，资源 Block 保存 href 和完整 iCalendar/vCard；`member` Relation 是唯一集合成员事实。Resolver 派生标题与文本，不另存可写精简模型。

Radicale `acquire_lock` 映射一个 graph UoW；Core repository 提供 PostgreSQL 事务 advisory lock，覆盖不存在资源的创建以及 ETag 检查到写入提交。协议处理使用该事务内的内存图快照，退出时只写变化部分；提交成功后才发送 HTTP 成功。个人集合规模下先扫描该扩展图，不增加缓存和索引。

写事务按集合 Block → 成员 Relation → 资源 Block 取得行锁，保护普通 Peer 的直接修改；读事务采用 REPEATABLE READ，防止跨查询看见不一致的图。同步 main 后复用其 `acquire_transaction_lock`，增加可选共享锁；Host SDK 升为 0.3.2，CalDAV 声明 `>=0.3.2 <0.4.0`，其他扩展兼容范围保持有效。Radicale MOVE 的源 ETag 检查由扩展补齐；已存在集合不可整体替换。

sync-token 由当前有序内容计算；相同 token 返回无变化，变化时旧 token 失效并完整重扫。此方案的准入条件是 DAVx⁵ 真机重扫正确处理删除、离线变化；不满足则修正同步实现，不能宣布验收完成。

执行顺序：
1. 实现协议入口、图适配和 resolver，验证同一旧 ETag 的并发 PUT 恰好一个成功、另一个 412，以及失败不提交。
2. 完成多集合、字段往返和真实 PostgreSQL HTTP 集成检查，验证认证轮换与路由撤销。
3. 完成独立 wheel、锁文件、发布片段与使用／维护文档。
4. 华为真机接入并验证三类对象双向同步、重复与删除、离线重连；保留证据。

华为已成功安装从本机小米读取的 DAVx⁵ 4.5.14-ose 与 Tasks.org 15.3 APK（只复制安装包，不复制账号或应用数据）。worktree 的 SVC 隔离 PostgreSQL 已健康；最初 ensure 的探测早于 runtime.json 落盘，后续 status 已确认 ready/provider/source 均匹配。

## 本地实现与验收记录（2026-10-20）

技术方案、验收设计、实施准备、实现与本地验证已完成。后续 preview 结果见下节。持久设计见 `docs/30-unit-tdd/caldav-extension.md`；安装连接说明见 `extensions/caldav/docs/python/index.md`。本地初始 wheel 位于 `.runtime/caldav/wheels/inkcre_ext_caldav-0.1.0-py3-none-any.whl`，已通过 build-shape 校验。

自动化证据：

- CalDAV PostgreSQL HTTP 集成：6 passed。覆盖原生内容与 Resolver、并发 ETag、提交失败回滚、MOVE 版本保护、集合删除与另一 Peer 成员移动／新增竞争、同步令牌失效、密码撤销与路由撤销。
- Memos PostgreSQL 集成：16 passed，验证共享图持久化接口的既有行为。
- 既有扩展安装、分发、配置和生命周期相关测试：25 passed。
- foundation、lock current、全仓 Ruff／格式、全仓 Pyrefly、数据库边界、发布契约、wheel 构建与校验、diff whitespace 检查通过。类型检查有 vobject 缺少 stubs 与测试 SQLModel deprecated API 的提示，无错误。

真机使用唯一测试账户 `inkcre-caldav-test@example.invalid`，通过 `adb reverse tcp:18789 tcp:18789` 连接工作树服务；只创建或修改显式测试数据。

| 行为 | 实际观察 |
| --- | --- |
| 发现与认证 | DAVx⁵ 成功发现 calendar、tasks、contacts，分别勾选同步；三类对象进入手机 |
| 日历 | 系统日历显示 GMT+8 的正确时间、每天共 3 次及提前 15 分钟提醒 |
| 离线例外 | 断开该 USB 转发，在系统日历修改“此日程”；重连后同一资源包含原 RRULE 和一个 RECURRENCE-ID 例外；手机 provider 中 master/exception 分别为 833/834 |
| 任务双向状态 | Tasks.org 修改标题、完成后，服务端保存 SUMMARY、STATUS:COMPLETED、COMPLETED 时间；服务端恢复未完成后手机重新显示任务 |
| 删除与重扫 | 服务端删除旧测试任务后 Tasks.org 列表为空；DAVx⁵ 在收到 valid-sync-token 403 后重新 REPORT 并收敛 |
| 手机新建 | Tasks.org 新建任务及子任务；服务端取得两个 VTODO，保留 RELATED-TO 父子引用 |
| 联系人 | 服务端姓名修改进入系统通讯录；手机修改姓名回写 vCard，两个电话号码保留；照片进入 Android photo 数据行，再次在手机编辑姓名并上传后 PHOTO bytes 与两个号码仍保留 |
| 重启 | 重启本地服务后，2 个 VEVENT components、2 个 VTODO、1 个 vCard 的完整集合文本与 ETag 均与重启前相同 |

截图与日志在 `.runtime/caldav/evidence/`，包括日历重复与提醒、手机任务编辑、服务端恢复任务、删除后空列表、联系人照片。初始进程日志保存在 `first-session.log`；重启后日志为 `.runtime/caldav/server.log`。密码只保存在权限 0600 的 `.runtime/caldav/password`，不写入本文。

开发服务通过 `.runtime/caldav/run.py python .runtime/caldav/serve.py` 启动；该脚本仅为本机隔离验收组合真实 Extension 生命周期和图存储，不作为发布部署入口。手机账户依赖 USB 转发与此进程存活，未配置永久 HTTPS 服务。未做大规模集合性能或其他客户端兼容测试；变化后的完整重扫是已明确的规模限制。

参考：https://tasks.org/docs/sync_davx5/ 、https://manual.davx5.com/accounts_collections.html 、https://manual.davx5.com/tasks_notes.html 、https://www.rfc-editor.org/info/rfc4791/

## PR、preview 与教程（2026-10-20）

已同步 main `10ac178`，复用其事务锁接口并将 Host SDK 下限更新为 0.3.2。PR：
https://github.com/InKCre/impl-py/pull/146 。功能验收源码为 `92f9324`；后续提交补充教程、
截图和验收记录，不改变运行实现。未合并、未正式发布 Registry Release、未操作生产环境。

preview Core：https://inkcre-core-py-pr-146-5bf318fbfb58.herokuapp.com/ 。
Registry：https://pr-146.inkcre-core-py-extension-registry-preview.pages.dev 。
部署 run `39424964601` 成功。通过 Core `selection` 安装独立 wheel，等待进程重启后启用，
确认 `loaded_version=0.1.0`、`running=true`，再设置专用密码；没有使用本地测试发布钩子。

| preview 观察 | 结果 |
| --- | --- |
| 三类集合与双向同步 | DAVx⁵ 登录、集合选择成功；系统日历、Tasks.org、联系人均出现 preview 数据 |
| 离线单次例外 | 断开 DAVx⁵ 的网络隧道后编辑一次日程，服务端仍为旧内容；重连后同一资源保留 master 与 RECURRENCE-ID |
| 任务 | 手机改名和完成进入服务端；服务端恢复未完成进入手机；手机新建父子任务，RELATED-TO 指向正确父 UID |
| 联系人 | 手机姓名上传；服务端姓名与照片下载；手机再次改名后，服务端仍有照片 bytes 和两个电话 |
| 全天事件 | 华为系统日历创建的无效 ZoneId 导致 DAVx⁵ 本地失败，两种创建流程均复现；改用 Etar 1.0.57 后保存 UTC，上传 DATE 范围 2026-10-20 至排他结束 2026-10-21 |
| 冲突与删除 | 旧 ETag PUT 返回 412；服务端删除旧测试任务，后续重复同步检查其不存在 |
| 信息权威 | PostgREST 读到相同原生资源内容；4 个 collection Blocks 包括 principal 和 3 个业务集合 |
| 生命周期 | 停用后 DAV 404；重启用内容不变；撤销密码 401、恢复后 207 |

华为使用测试账户 `preview146@example.invalid`。系统日历的 master／例外 ID 为 835／836，
Etar 全天事件为 839，联系人为 130；两条无法上传的华为全天测试事件已通过界面删除。
Etar 从 F-Droid 官方包安装，只授予日历读写权限。DAVx⁵ 已允许后台运行。

手机直连将 Heroku 域名解析为错误地址并超时；本次采用 DAVx⁵ 的 HTTP 代理
`localhost:18790`，通过 `adb reverse tcp:18790 tcp:18790` 接入本机仅允许该 preview
主机 443 端口的 CONNECT 转发。TLS 与密码仍由手机和 preview 处理，未关闭证书校验。
转发脚本为 `.runtime/caldav/preview/tunnel.py`。该网络条件依赖 USB 和本机进程；
切换到能够直接访问部署的网络后，在 DAVx⁵「设置 → 代理类型」恢复「系统默认」。

教程：`extensions/caldav/docs/global/android.md`；服务端步骤：
`extensions/caldav/docs/python/index.md`。13 张真实手机截图位于相邻 `images/`，
两套 VitePress 文档均已构建通过。截图使用测试内容，密码保持遮蔽。

验证补充：同步 main 后 22 项 DAV/Memos PostgreSQL 回归通过；严格 Pyrefly 已为 0
diagnostics，pre-commit 全部通过。GitHub run `39424968303` 的仓库检查为 60 passed、
64 条按环境条件跳过，CLI 与可移植数据库运行时检查通过。CalDAV 的 6 项真实数据库测试
已另行执行通过，不能把 CI 中的跳过视为数据库测试证据。

原始响应、受限日志、生命周期检查和重新部署前的集合快照保存在忽略目录
`.runtime/caldav/preview/`。专用密码仅在其权限 0600 的 `password` 文件。最终 PR
检查与部署状态由 GitHub checks 提供；保留的规模限制仍是全图事务和变化后的完整重扫。

## Web 分发补齐（2026-10-20）

用户追加要求 setup wizard 与 content render；继续沿用独立 worktree、免人工阶段核对、
提交推送与 PR、preview 验收及教程截图授权。TS 工作树为相邻
`/Users/lanzhijiang/Development/.worktrees/caldav-extension/impl-ts`，
从 `origin/main` 的 `2d260e3` 创建 `feat/caldav-extension`；不修改原 TS/UI 工作区的在途变更。

产品范围为连接准备和只读内容阅读：选择能执行 Python 分发的 Peer，保留已有版本／密码，
缺失时显式安装并生成密码，确认重启完成及实际运行后提供地址、用户名与遮蔽密码。
集合、事件／任务、联系人均接入现有 Info-base 预览和内容视图；不增加完整日历编辑器。

顾问核对后采用现有 setup contribution 和三个 resolver。选择发行版时使用目标 Peer 的
Registry；不能用浏览器 Registry 替代。select 后观察 restart_required／operation，
未知写入结果先刷新原目标，不自动重发。准备完成要求 running、loaded_version 与 selection
相符及连接接口成功。原生 iCalendar/vCard 使用 ical.js 解析，只生成内存展示投影；
保留 DATE、源墙上时间与 TZID／UTC，主事件与每个例外分别展示，不展开未来重复次数。
照片只自动显示嵌入式栅格数据，远程 URI 保留显式链接；解析失败保留原文和错误。

实施顺序：新增 MF 包与解析／渲染；实现安装重启感知的向导；同步构建、preview inventory、
发布意图与文档；验证真实手机数据、未知结果恢复及窄屏显示，再交付 TS PR 与截图。
终验使用现有 Python PR #146 preview 的持久数据，确认 Web 打开与刷新不写配置、
不重置密码，信息预览和完整内容能够读取事件例外、全天日期、父子任务和联系人照片。

Web PR：https://github.com/InKCre/impl-ts/pull/142 。preview：
https://preview-client-web-pr-142.inkcre-client-web.pages.dev/ 。Web preview 分发当前版本为
`0.0.0`，首次正式发布由 minor Changeset 决定；Python preview 分发保持 `0.1.0`。

Web 验收已经观察到：

- 从扩展卡片实际安装并启用 MF，打开「设置」直接取得已运行 Python Peer 的连接信息；
  密码默认遮蔽，主动显示／隐藏及复制可用。打开与刷新前后，共享扩展记录 SHA-256
  一致，未改密码、配置或原生内容。
- 冷启动直达事件、联系人、全天事件、任务集合内容链接均恢复 MF renderer。主事件与
  离线例外、原始 UTC、15 分钟提醒、全天排他结束日期、照片及双电话号码均可读。
  集合成员按钮跳转子任务，显示 Tasks.org 父 UID。390px 与桌面截图已保存。
- 浏览器受控响应模拟（拦截目标管理写入，未发送到真实服务）覆盖首次 select 仅一次、
  重启期间停止写入、enable 响应丢失后锁定原目标、刷新读到 downloading 仍阻止重发、
  failed + restart_required 明确显示失败且保持禁止写入。后两种状态没有增加写请求。
  移除模拟后刷新重新显示真实连接。模拟脚本在 TS `.runtime/caldav/setup-faults.js`。
- 10 项解析／状态／配置契约测试通过；最新全仓 `pnpm check` 通过。日历内容 axe 为
  0 violations、0 incomplete；窄屏向导和联系人为 0 violations，但 axe 对折叠下拉
  aria-controls 与叠层背景对比度留有人工判断项，不能宣称完整 WCAG 认证。

首次安装联调发现 Python Peer 的 Registry override 可为 null；本 preview 的有效默认
来自进程环境，shared configs 中没有 extension.registry。向导现仅接受目标 override 或
共享显式配置，缺失时提示设置目标 Registry，不猜浏览器默认。通过 Python Peer 原生
配置表单填写 `https://pr-146.inkcre-core-py-extension-registry-preview.pages.dev`，
确认持久配置仍仅有原来的两个 Python 字段，公共 URL 不变。已有 selection 不需要补地址。
没有扩展 management v2 响应字段，避免旧 Python 严格 schema 与 Runtime 协调升级。

本机浏览器对代理 DNS 的 Heroku 虚拟地址直连失败。验收浏览器使用隔离 profile 与
仅允许 Web/Python 两个 preview 及其 Registry 域名的 loopback CONNECT 代理 18792；
TLS 证书校验保持启用。原华为手机的 18790 USB 隧道未改动。Web 教程和 9 张截图位于
TS `extensions/caldav/docs/module-federation/`，不含密码明文。TS 功能源码为 `9b0875e`，教程提交为 `e868fef`；最终检查状态由各 PR checks 提供。

最新补充：Registry 缺失模拟显示明确配置提示且写请求为 0；恢复显式配置并刷新后，
只发送一次 Python 0.1.0 的 select。信息图已显示集合与联系人的自定义预览。
独立工作树保留用于 PR 审阅，原 checkout 的无关修改未纳入提交。
