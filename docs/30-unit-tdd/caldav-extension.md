# CalDAV / CardDAV 扩展

`inkcre/caldav` 提供共享部署内的日历、任务和联系人读写。Radicale 3.8.3 拥有 DAV
发现、XML、过滤、重复规则和原生内容校验；扩展拥有配置认证、图映射、事务、
Resolver 和 Host 生命周期。协议层不使用 Radicale 文件存储，不另外建立 DAV 数据库。

## 信息权威

| Resolver | Block 内容 | 关联 |
| --- | --- | --- |
| `extensions.caldav.collection.v1` | `path` 与 DAV `props` | 出向 `member` Relation 指向资源 |
| `extensions.caldav.calendar.v1` | `href` 与完整 iCalendar `text` | 恰好一个集合拥有 |
| `extensions.caldav.contact.v1` | `href` 与完整 vCard `text` | 恰好一个集合拥有 |

路径相对于 `/caldav/`，principal 固定为 `inkcre`。资源的 UID、重复、时区、提醒、
任务父子关系、联系人照片和扩展属性保存在原生文本，不另存可写字段副本。文本经
Radicale/vobject 校验和规范化，不承诺输入字节不变。Resolver 输出完整原生内容并派生
标题，允许普通信息库读取；集合成员关系只有 Relation 一份权威。

不重复保存 UID 索引或 ETag。ETag 从内容计算，集合 token 从有序资源及集合属性计算。
未知 token 返回 `403 DAV:valid-sync-token`，客户端须完整重新枚举；相同 token 返回无变化。
这一策略避免删除日志，但放大变化后的读取量。

## 事务与并发

FastAPI 在认证和有界读取请求后，使用 AnyIO 工作线程调用 WSGI。Storage 的
`acquire_lock` 经 `anyio.from_thread.run` 进入同一事件循环上的 graph UoW。
事务期间只有协议计算与数据库操作，没有外部网络调用；响应要等事务提交后才返回。
取消不会丢弃仍运行的线程，异常通过 UoW 回滚。

命名空间 `extensions.caldav.v1` 的 PostgreSQL 事务 advisory lock 在不同 Python
Peer 之间串行化 DAV 写入，并保护还没有行可锁的新集合／资源身份。写事务依次锁定
集合 Block、成员 Relation、资源 Block；集合的 FOR UPDATE 也阻止并发外键成员插入。
这保护普通信息库对现有 Block／Relation 的直接编辑。新增集合以及路径／UID 唯一性的
写入仍须遵守该扩展契约和命名空间锁，不能靠任意原始图写入绕过业务校验。

读事务采用 REPEATABLE READ，防止分次读取 Block 和 Relation 时看到不同的已提交状态。
事务内的图快照只负责协议执行，退出写事务时提交差量，不是第二份持久权威。
不增加按资源的事务或后台同步队列；ETag 检查和最终写入在同一个锁范围内。

Radicale 的 PUT 校验当前 ETag；扩展另外在 MOVE 的写锁内校验源 ETag，过期或缺失时返回
412。首次并发发现可能重复请求默认集合创建，Storage 拒绝替换已存在集合，防止覆盖数据。
集合整体覆盖 PUT 被关闭；普通集合属性修改和单资源替换仍受支持。

当前每次事务读取所有 DAV 集合与资源，写事务锁住该扩展图。对于个人规模，这使事务、
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
