# CalDAV / CardDAV 扩展

`inkcre/caldav` 提供共享部署内的日历、任务和联系人读写。Radicale 3.8.3 拥有 DAV
发现、XML、过滤、重复规则和原生内容校验；扩展拥有配置认证、图映射、事务、
Resolver 和 Host 生命周期。协议层不使用 Radicale 文件存储，不另外建立 DAV 数据库。

## 信息权威与复用

| Resolver | Block 内容 | 关联 |
| --- | --- | --- |
| `extensions.caldav.collection.v2` | `path`、DAV `props`、`published` | 出向 `member` Relation 发布资源 |
| `extensions.caldav.calendar.v2` | `href`、`remainder` | 指向独立正文、附件和相关对象 |
| `extensions.caldav.contact.v2` | `href`、`remainder` | 指向独立电话、邮箱、备注、URL 和媒体 |

`remainder` 是剔除图拥有属性后的原生内容。名称、日期、状态、重复规则、时区和未知扩展属性
仍属于对应对象；完整 iCalendar/vCard 由剩余字段与当前 Relation 目标组合生成，不另存可写全文。
事件系列与单次例外保留在同一资源，组件以类型、UID 和完整 RECURRENCE-ID 属性定位，包含值类型
与 TZID；提醒中的独立内容另以 VALARM 顺序定位。

TEL、EMAIL、URL、NOTE、DESCRIPTION 使用普通 `core.text.v1` Block。PHOTO、LOGO、SOUND、ATTACH
的内嵌字节使用既有 PostgreSQL binary Storage 和 `core.image/audio/video/pdf/file.v1` 等语义
resolver；按声明 MIME、字节签名、通用 file 的顺序分类。URI 形式的媒体使用文本 Block 保存原地址，
不自动联网下载。其他能力可以直接读取、处理、引用这些内容，不需要先解释整张名片或日程。

出向 Relation 内容使用 `caldav:property:` 加 JSON，其字段为 `component`、`alarm`、`name`、
`params`、`group`、`position`、`representation`、`media_type`。它独占属性所属组件、协议参数、
标签、顺序和编码方式，目标 Block 独占值。组件内的 position 按属性名分别从零计数；允许删除后
存在空位，但不允许重复位置。存在同名 native 属性与图属性时明确拒绝，不能选一个掩盖冲突。

RELATED-TO 优先指向同集合中 UID 唯一匹配的资源 Block；目标尚不存在或有歧义时指向保存原 UID 的
文本 Block，不猜测实体。后续 DAV 写入和启动迁移会重新解析同集合引用，因此先创建子任务也能在
父任务出现后形成真实 Relation。目标退出集合后，已有引用仍指向该信息对象。

手机更新在当前资源内按组件、属性名、表示方式和值匹配原有目标；只改标签或顺序不重建内容。
不同值创建新的内容并更换 Relation，避免改写其他对象已经引用的旧事实；不按号码或内容在全库
隐式合并对象。其他 Peer 主动编辑共享内容 Block 或其字节则会影响全部引用者，下一次 DAV 读取
直接反映修改。新增或直接修改图的 Peer 必须遵守上述语法及资源路径、UID 的领域约束。

DAV DELETE 只撤销 `member` 发布；删除集合将其 `published` 设为 false，并撤销成员发布。
资源、内容及其他图的引用继续存在，可以在信息库中阅读和复用。此版本不自动回收未被引用的旧值，
也不把撤下同步等同于彻底删除个人信息；需要彻底删除时应由信息库删除命令明确选择对象。

v1 解码器保留。扩展启用时在一个事务内将已有资源提升到 v2，保留根 Block ID、外部关系、路径与
凭据；失败整体回滚。随后新出现的 v1 记录在下一次 DAV 写事务中提升。升级后导出格式可能规范化、
ETag 可能改变，客户端重新扫描；不承诺原始输入字节不变。降级到只理解 v1 的旧分发不受支持。

路径相对于 `/caldav/`，principal 固定为 `inkcre`。集合 token 从当前合成资源及集合属性计算。
未知 token 返回 `403 DAV:valid-sync-token`，客户端须完整重新枚举；相同 token 返回无变化。
这一策略避免删除日志，但放大变化后的读取量。

## 事务与并发

FastAPI 在认证和有界读取请求后，使用 AnyIO 工作线程调用 WSGI。Storage 的
`acquire_lock` 经 `anyio.from_thread.run` 进入同一事件循环上的 graph UoW。
事务期间只有协议计算与数据库操作，没有外部网络调用；响应要等事务提交后才返回。
取消不会丢弃仍运行的线程，异常通过 UoW 回滚。

命名空间 `extensions.caldav.v1` 的 PostgreSQL 事务 advisory lock 在不同 Python
Peer 之间串行化 DAV 写入，并保护还没有行可锁的新集合／资源身份。写事务依次锁定
集合与资源根、相关 Relation、语义 Block 和实际 PostgreSQL blob，并在锁定后读取值。
根与语义 Block 的 FOR UPDATE 阻止并发外键引用插入；Relation 和 blob 行锁覆盖普通 Peer
直接修改关系与字节的路径。ETag 由本次合成的完整协议内容计算，不以根时间戳代表媒体新鲜度。新增集合以及路径／UID 唯一性的
写入仍须遵守该扩展契约和命名空间锁，不能靠任意原始图写入绕过业务校验。

读事务采用 REPEATABLE READ，防止分次读取 Block 和 Relation 时看到不同的已提交状态。
事务内的图快照只负责协议执行，退出写事务时提交差量，不是第二份持久权威。
不增加按资源的事务或后台同步队列；ETag 检查和最终写入在同一个锁范围内。

Radicale 的 PUT 校验当前 ETag；扩展另外在 MOVE 的写锁内校验源 ETag，过期或缺失时返回
412。首次并发发现可能重复请求默认集合创建，Storage 拒绝替换已存在集合，防止覆盖数据。
集合整体覆盖 PUT 被关闭；普通集合属性修改和单资源替换仍受支持。

当前每次事务读取所有 DAV 集合与资源及其属性，包含已撤下发布的对象，写事务锁住这些依赖。对于个人规模，这使事务、
故障恢复和直接图编辑的行为清晰；需要大型通讯录时再引入按集合加载和增量历史，
必须同时保留跨集合移动与删除的并发保证。

## 路由与配置

`Extension.api_dependencies()` 选择协议自有认证。DAV 请求使用 HTTP Basic，
用户名固定为 `inkcre`；每次读取共享 `password` 配置，空值拒绝访问。只有认证后的
principal 通过 WSGI `REMOTE_USER` 传入，客户端不能提供此身份或覆盖 SCRIPT_NAME。
请求体最多 16 MiB，单资源最多 8 MiB；载荷和认证信息不进入协议调试日志。

`caldav.connection.v1` 使用 Peer JWT，只返回公共基址组成的服务地址和用户名。
普通 DAV 凭据不能读取该管理接口。所有路由通过 Extension Host 发布与撤销；
停用不删除图，重新启用不重建已有集合。

## 验证依据

`tests/extensions/caldav/test_dav_graph.py` 使用显式选定的已迁移 PostgreSQL，
从 HTTP 边界检查原生字段往返、Resolver 读取、双写 ETag 冲突、提交失败回滚、
移动的版本约束、直接 Peer 成员修改与集合删除竞争、密码撤销及路由撤销。
这些检查保护图适配与事务行为，不重复测试 Radicale 的完整协议实现。

2026-10-20 在华为 JKM-AL00a（Android 9）上使用 DAVx⁵ 4.5.14-ose、Tasks.org 15.3
及系统日历／通讯录完成了真实同步验证：重复事件与提醒、离线修改单次例外、任务完成与
恢复、手机新建父子任务、服务端删除、联系人双向修改及照片往返。DAVx⁵ 在旧 token
失效后能够重新同步。服务重启后原生内容和集合 ETag 保持一致。该记录不代表其他客户端
版本或大型集合已经验收；协议集成检查也不能替代 Android 字段映射证据。

同日 PR #146 的 HTTPS preview 通过独立 Registry wheel 选择、进程重启和正常启用流程，
再次验证了上述双向同步、父子引用、照片往返和旧 ETag 的 412 响应。停用返回 404，
重新启用后内容不变；撤销密码返回 401，恢复密码后恢复访问。PostgREST 能读取同一批
原生资源 Block。手机当前网络的域名解析异常，验收使用 DAVx⁵ 应用内 HTTP 代理和
仅允许该 preview 主机的 CONNECT 隧道；TLS 仍由手机与 Heroku 终止。

华为 JKM-AL00a 的系统日历在两种新建流程中都将全天事件的 `eventTimezone` 写成
`java.util.SimpleTimeZone[...]`。DAVx⁵ 因无效 ZoneId 在上传前失败，服务端无法修复
尚未发出的请求。Etar 1.0.57 使用同一 Android 日历存储写入 `UTC`，全天事件已成功
上传为 DATE 类型，并保留正确的排他结束日期。此设备的全天事件创建以 Etar 为验收
基线；系统日历的重复事件显示、单次例外编辑仍有独立通过证据。
