# Python Twitter 采集与归档

Python Twitter distribution 把 x-monkey 提交和 Bookmark 采集写入同一组普通 Blocks。共同字段与跨分发关系由 `inkcre/twitter` 的 [Twitter 归档契约](../_shared/20-product-tdd/twitter-archive-contract.md) 拥有，`model.py` 实现独立的 User/Post/Media、身份根与捕获集合。现有 JSON 字段、Resolver ID 和 Block ID 保持不变，数据库没有另一套归档表。

`archive_schema.py` 只保存 x-monkey 0.2.0 的固定协议 DTO；`x_monkey.py` 把已校验的提交转成共同模型，并按固定 DTO 字段集递归投影 query 数据、构造协议状态与汇总。自由的 Article JSON 按值保留。共同模型未来新增的字段不会自动进入旧协议，无法用该协议表达的已知结构返回错误，不改写持久事实。Official/Twikit 适配器在 `api.py` 中直接产出共同模型，`bookmark.py` 与 `resolver.py` 不依赖协议 DTO。当前适配器保留它们明确映射的事实，不宣称保存完整 provider 原始响应或所有未知字段。

`reconcile.py` 拥有本实现的身份选择、事务合并和关系维护，只返回变化集合、Block IDs 及共同读取结果，不包含 HTTP 状态或 wire 响应类型。`TwitterBatch` 没有继承远端请求的条数限制；每类 1000 条的准入边界留在 x-monkey DTO。

一次 submit 使用一个 `graph_uow()`。Bookmark 先完成 provider 请求，再进入 `source_uow()`，锁定 Source，使用其 `graph` 合并新主帖及实际捕获的上下文，并在同一事务内推进 cursor。主帖顺序与展开上下文分开，因此引用帖不会成为下一次书签采集的 watermark；旧的数值 cursor 在比较边界转换为十进制字符串，不重置历史状态。

## Bookmark 归属与历史证据

`source_writer.py` 在原生 archive 之外写入账号级事实，Relation content 使用字面值：

```text
authenticated Twitter User --bookmarks--> selected primary Post
```

actor 必须由实际执行本次 Bookmark fetch 的 client 绑定到 `TwitterAPIResult.bookmark_user_id`。Official 使用本次请求 `/users/{id}/bookmarks` 的账号 ID；Twikit 登录后通过同一 client 的公共 `user_id()` 获取身份（cookie 登录不一定填充私有缓存）。缺少或无效身份显式失败，不能使用 Post 作者、Source 配置、用户名或提交时重新读取的全局账号。网络访问在数据库事务外完成。

Bookmark collect/backfill 不调用 `SourceManager.ensure_block_async`，不创建或刷新 `core.source.v1` anchor，也不写任何 Source provenance 关系。新建的纯 Bookmark Source 成功采集后，`sources.block` 仍为 `NULL`；已有 anchor 和历史关系保持原样。SourceModel、Job、Source 行锁、cursor 和诊断继续承担运行职责，不自动成为知识关系。此取舍落实 [Issue #184](https://github.com/InKCre/impl-py/issues/184) 的 Twitter 合同；跨 Source 原则修订在 [Issue #196](https://github.com/InKCre/impl-py/issues/196) 待评审，不在这里改变 Mail、GitHub 或通用 lazy anchor 行为。

账号使用 `extensions.twitter.user.v1` 原生根；`persist_twitter` 的显式 `user_identities` 在同一 advisory lock 下协调 identity-only `{id}`，不伪造 handle/name，不放宽完整 User 或 Resolver 合同。已有完整资料保留，后续普通 archive submit 可以原地补齐身份根。

账号根、收入的 Post/User/Media 与引用、账号→主帖 `bookmarks` 及普通 watermark 在同一事务提交。backfill 逐页原子提交，Job 进度只报告已经提交的页面，不推进普通 watermark。重复捕获使用现有 Source 行锁及 Twitter 事务锁，按 `(from, content, to)` 去重，不删除其他账号的证据。删除 operational Source 不影响账号收藏事实或既有历史 anchor/关系。

`bookmarks` 只来自本次选中且实际捕获的 primary IDs；quote/repost/reply、其作者、媒体、ID-only 引用或未选中的主帖不会因此成为收藏。不能使用 reconciliation 的全部 `block_ids` 或 `changed` 推断收藏。不同 Sources 的同一账号复用一条账号→Post 边；不同账号保留各自边，共用原生 Post 根。

`bookmarks` 是历史正向观察证据，不是远端当前状态的完整快照，也不是收藏动作时间。没有 `bookmarks` 表示未知，不表示未收藏。部分页面、空响应、full/backfill 缺席或取消收藏不撤销已有证据。不提供逐 Post 的 Source/Job 血缘，也不通过 Source 当前配置反推历史 actor；既有共享授权、账号切换、并发 fetch 与 watermark 风险独立存在。

既有 v1/v2 Resolver、原生 bookmark count、旧 `bookmarked for`、旧 Source 范围关系和普通 archive submit 都不能证明账号 Bookmark 归属。不执行猜测性迁移或自动清理；历史未知保持未知，旧 note/用户关系不改写。显式 full/backfill 重新观察选中的主帖后可原地补证并保留 Block ID。普通增量只收入旧 marker 之前的主帖，marker 本身及之后的历史数据不会自动获得证据；不要重置 marker 来伪造回填。旧 `_organize()` 不恢复副作用。

两条写入路径都先取得 `extensions.twitter.reconcile` 全局事务 advisory 锁，再批量重读原生身份。根内容和受管关系一起提交；异常让整批回滚，调用方可安全重放。该锁仅约束正常 Twitter 写入入口，不能替代所有 info-base 原始写入的约束。provider 和媒体网络访问都不能放进此事务。

原生身份、Blocks 和引用边使用批量查询与创建。不要在每个媒体项内单独查询、创建和 flush：Core 与数据库跨网络部署时，这会把一次正常批次放大为数百次串行往返，并在持锁期间耗尽调用方的同步请求期限。

匹配先选新版 resolver，再按 Block ID 稳定选择旧 `tweet.v1`。旧根重新采集后原地更新，保留现有 Block ID、引用和旧附件关系；未重新采集的旧根仍由 v1 decoder 读取。旧加工正文不足以重建 native RichText，不能用它伪造新记录。共同合并提供 `replace` 与 `supplement` 正文策略：正常捕获合并已提交的正文结构，补充捕获在已有正文时保留它并增加其他事实。Bookmark 使用补充策略，避免较少详情的正文覆盖完整捕获；该选择不作为字段写进持久记录。

身份引用严格保存为 `{id}`，不补作者、时间或正文。query 对完整 native 记录返回 `found`，身份引用返回 `missing`，能按 ID 定位但结构损坏的记录返回 `failed`。批量身份查询使用有效 JSON 的条件表达式隔离普通文本和损坏 JSON；无法解析且无法定位 ID 的内容不会命中某个原生 ID，不会导致其他正常记录的查询失败。

共同合并选择缺省保留、对象递归合并和数组整体替换，保留有序媒体引用。原生观察时间保存输入字符串，不在序列化时改写精度。Twitter 以毫秒分辨率逐计数比较观察时间；较旧观察不覆盖新值，相同值的新时间也更新持久记录，无时间的新值删除该计数的旧时间标签。这些是共同语义的规则，x-monkey 兼容基线用于验证其适配结果。协议 Adapter 将无持久变化映射为 `unchanged`，仅观察时间更新仍为 `accepted`。

新版引用边只维护约定的 reference schema，旧 `attachment:*`、`entities:url` 和用户关系保留。媒体引用顺序是 `post.mediaIds`、文章封面、`article.mediaIds` 依次合并去重，首次出现保留。Media 根只保存原生 JSON；写入不创建 `core.image.v1`、`core.video.v1` 或 Media→`content` 关系。较早版本产生的内容块和边保留，须先核对其他引用及来源，再由明确的数据清理处理，正常重放不会删除它们。

媒体原生 URL、variants 和 alt 按捕获值保留，Python 不筛选或规范化这些字段。保存、query、文本投影和索引只消费元数据；用户显式展开媒体时，消费端 Twitter Distribution 才选择图片地址或最高 bitrate MP4，并交给现有 HTTP Storage。HTTP(S) 地址、重定向、超时与体积限制由通用 Storage 合同处理，不增加 Twitter 专属 CDN、HTTPS/443 或禁用重定向策略。HLS 继续保留为元数据。本版本不保存媒体本体，也不创建下载 Job，外部来源失效后仍可读取文字与元数据。

此边界通过减少持久块实现。Core 的普通图片和视频 resolver 在 lexical 索引中仍可读取字节并生成 OCR/STT；如果 Twitter 提前建立这类内容块，独立的索引扫描会触发读取，即使 Post/Media 自身的投影不展开媒体。Twitter 原生记录因此不预建内容块，其他来源的通用 lexical/OCR/STT 行为保持原有合同。

Python 的 v2 Post 文本投影包含原帖 ID、链接、已捕获作者、正文和长文；User/Media 投影使用自身已经捕获的文字。label 保持 Block-local，不为投影或标签下载媒体。检索沿用现有独立索引维护流程，不在提交中隐式维护索引。

Python Twitter 在 `/twitter/x-monkey/account` 自托管连接页面。用户只在自己信任的 HTTPS 部署输入已有 `JWT_SECRET`，页面使用 WebCrypto HMAC SHA-256 本地签发 60 秒普通 Peer JWT，再以同 Origin Bearer 和 JSON `{}` 请求 `POST /twitter/x-monkey/session`。该路由复用 `require_peer_jwt`，拒绝非 Core Origin、非 JSON 和带 `session_use` 的凭据，返回 `SessionResponse` JSON 并建立归档 Cookie。页面随后 GET `api/v1/session`，确认 Cookie 的 `authenticated=true` 后才显示 `Connected`、Peer 和到期时间。

账户页以随机 nonce CSP 限定脚本，`connect-src 'self'` 限定连接目标。JWT 不进入 URL，密钥不持久化或发送给服务器。页面不加载第三方脚本，不接受任意回跳；浏览器内签名不使恶意或被篡改页面安全。

连接不要求 Web 或 MF 分发。`setup.x_monkey_base_url` 继续供用户复制，Web Setup 只提供通向该 Core 账户页的普通链接，不持有此连接流程的密钥、JWT 签发或凭据传输逻辑。账户页显示 Peer 与到期时间，脚本通过会话检测确认连接。七天 Cookie 默认期限与协议续签仍由 Python Twitter 拥有。

Bookmark 归属回归在 `tests/extensions/twitter/integration/test_bookmark_membership.py`，通过生产 collect/backfill 入口与独立数据库会话读取，验证账号/作者区分、主收藏/上下文、跨 Source 同账号去重与多账号共用 Post、身份根补齐、历史重观察、Source 删除及页事务回滚，并检查新 Source 不生成 anchor（含未绑定的孤立 anchor）、旧 anchor 不刷新且历史 Source 关系不变；由 `pdm run test:database` 在可丢弃 PostgreSQL 上执行。无显式数据库时默认 pytest 跳过这些集成场景。

其他候选验证脚本与 fixture 放在 `scripts/tests/twitter`，默认 pytest 与 CI 不收集。离线合同与 HTTP 边界验证入口为 `pdm run python -m pytest -q scripts/tests/twitter/test_archive_contract.py scripts/tests/twitter/test_archive_http.py`；显式运行整个目录可用 `pdm run python -m pytest -q scripts/tests/twitter`。

真实数据库场景要求显式提供 `INKCRE_TEST_DATABASE_URL`，使用已迁移的可丢弃 PostgreSQL runtime；测试只清理自己创建的精确 IDs，不重置开发库。原生 roundtrip 与真实 Twikit Bookmark 验证执行 `LexicalRetrievalManager.maintain` 的实际扫描、投影和检索链，在 HTTP Storage 字节边界断言零调用；同时检查归档不额外创建内容块、blob 或 Job。固定 fixture 同时用于原生 roundtrip 和独立上游合同验证，真实 userscript、HTTP 会话及 Web 消费仍需要各自的纵向验收。
