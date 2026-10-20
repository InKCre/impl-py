# Python Twitter 采集与归档

Python Twitter distribution 把 x-monkey 提交和 Bookmark 采集写入同一组普通 Blocks。共同字段与跨分发关系由 `inkcre/twitter` 的 [Twitter 归档契约](../_shared/20-product-tdd/twitter-archive-contract.md) 拥有，`model.py` 实现独立的 User/Post/Media、身份根与捕获集合。现有 JSON 字段、Resolver ID 和 Block ID 保持不变，数据库没有另一套归档表。

`archive_schema.py` 只保存 x-monkey 0.2.0 的固定协议 DTO；`x_monkey.py` 把已校验的提交转成共同模型，并按固定 DTO 字段集递归投影 query 数据、构造协议状态与汇总。自由的 Article JSON 按值保留。共同模型未来新增的字段不会自动进入旧协议，无法用该协议表达的已知结构返回错误，不改写持久事实。Official/Twikit 适配器在 `api.py` 中直接产出共同模型，`bookmark.py` 与 `resolver.py` 不依赖协议 DTO。当前适配器保留它们明确映射的事实，不宣称保存完整 provider 原始响应或所有未知字段。

`reconcile.py` 拥有本实现的身份选择、事务合并和关系维护，只返回变化集合、Block IDs 及共同读取结果，不包含 HTTP 状态或 wire 响应类型。`TwitterBatch` 没有继承远端请求的条数限制；每类 1000 条的准入边界留在 x-monkey DTO。

一次 submit 使用一个 `graph_uow()`。Bookmark 先完成 provider 请求，再进入 `source_uow()`，锁定 Source，使用其 `graph` 合并新主帖及实际捕获的上下文，并在同一事务内推进 cursor。主帖顺序与展开上下文分开，因此引用帖不会成为下一次书签采集的 watermark；旧的数值 cursor 在比较边界转换为十进制字符串，不重置历史状态。

两条写入路径都先取得 `extensions.twitter.reconcile` 全局事务 advisory 锁，再批量重读原生身份。根内容和受管关系一起提交；异常让整批回滚，调用方可安全重放。该锁仅约束正常 Twitter 写入入口，不能替代所有 info-base 原始写入的约束。provider 和媒体网络访问都不能放进此事务。

原生身份、Blocks 和引用边使用批量查询与创建。不要在每个媒体项内单独查询、创建和 flush：Core 与数据库跨网络部署时，这会把一次正常批次放大为数百次串行往返，并在持锁期间耗尽客户端的同步请求期限。

匹配先选新版 resolver，再按 Block ID 稳定选择旧 `tweet.v1`。旧根重新采集后原地更新，保留现有 Block ID、引用和旧附件关系；未重新采集的旧根仍由 v1 decoder 读取。旧加工正文不足以重建 native RichText，不能用它伪造新记录。共同合并提供 `replace` 与 `supplement` 正文策略：正常捕获合并已提交的正文结构，补充捕获在已有正文时保留它并增加其他事实。Bookmark 使用补充策略，避免较少详情的正文覆盖完整捕获；该选择不作为字段写进持久记录。

身份引用严格保存为 `{id}`，不补作者、时间或正文。query 对完整 native 记录返回 `found`，身份引用返回 `missing`，能按 ID 定位但结构损坏的记录返回 `failed`。批量身份查询使用有效 JSON 的条件表达式隔离普通文本和损坏 JSON；无法解析且无法定位 ID 的内容不会命中某个原生 ID，不会导致其他正常记录的查询失败。

共同合并选择缺省保留、对象递归合并和数组整体替换，保留有序媒体引用。原生观察时间保存输入字符串，不在序列化时改写精度。Twitter 以毫秒分辨率逐计数比较观察时间；较旧观察不覆盖新值，相同值的新时间也更新持久记录，无时间的新值删除该计数的旧时间标签。这些是共同语义的规则，x-monkey 兼容基线用于验证其适配结果。协议 Adapter 将无持久变化映射为 `unchanged`，仅观察时间更新仍为 `accepted`。

新版引用边只维护约定的 reference schema，旧 `attachment:*`、`entities:url` 和用户关系保留。媒体引用顺序是 `post.mediaIds`、文章封面、`article.mediaIds` 依次合并去重，首次出现保留。Media 根只保存原生 JSON；写入不创建 `core.image.v1`、`core.video.v1` 或 Media→`content` 关系。较早版本产生的内容块和边保留，须先核对其他引用及来源，再由明确的数据清理处理，正常重放不会删除它们。

媒体原生 URL、variants 和 alt 按捕获值保留，Python 不筛选或规范化这些字段。保存、query、文本投影和索引只消费元数据；用户显式展开媒体时，消费端 Twitter Distribution 才选择图片地址或最高 bitrate MP4，并交给现有 HTTP Storage。HTTP(S) 地址、重定向、超时与体积限制由通用 Storage 合同处理，不增加 Twitter 专属 CDN、HTTPS/443 或禁用重定向策略。HLS 继续保留为元数据。本版本不保存媒体本体，也不创建下载 Job，外部来源失效后仍可读取文字与元数据。

此边界通过减少持久块实现。Core 的普通图片和视频 resolver 在 lexical 索引中仍可读取字节并生成 OCR/STT；如果 Twitter 提前建立这类内容块，独立的索引扫描会触发读取，即使 Post/Media 自身的投影不展开媒体。Twitter 原生记录因此不预建内容块，其他来源的通用 lexical/OCR/STT 行为保持原有合同。

Python 的 v2 Post 文本投影包含原帖 ID、链接、已捕获作者、正文和长文；User/Media 投影使用自身已经捕获的文字。label 保持 Block-local，不为投影或标签下载媒体。检索沿用现有独立索引维护流程，不在提交中隐式维护索引。

Python Twitter 仅在 session 路由实际注册且具有 HTTPS 公共基址时广告 `inkcre.twitter.x-monkey.session.v1`，使用 `core.peer.protocol.browser-form.v1`，参数为 `method=POST`、`path=/twitter/x-monkey/session`、`credential_field=token`。Host/Auth 根据 selected Peer、有效 lease 和广告，从该 Peer 的 `http_public_base_url` 保留部署前缀派生 HTTPS 目标，内部签发新鲜 JWT 并发起顶层表单导航。`setup.x_monkey_base_url` 仍供用户复制展示，连接按钮是否可用独立取决于精确能力广告，展示值不决定凭据投递目标。Twitter UI 只提交连接意图，不接触 JWT 或任意目标 URL；返回 `initiated` 表示导航已启动，尚不能确认 Cookie 已连接。七天 Cookie 默认期限与协议续签仍由 Python Twitter 拥有。

候选验证脚本与 fixture 放在 `scripts/tests/twitter`，默认 pytest 与 CI 不收集。离线合同与 HTTP 边界验证入口为 `pdm run python -m pytest -q scripts/tests/twitter/test_archive_contract.py scripts/tests/twitter/test_archive_http.py`；显式运行整个目录可用 `pdm run python -m pytest -q scripts/tests/twitter`。

真实数据库场景要求显式提供 `INKCRE_TEST_DATABASE_URL`，使用已迁移的可丢弃 PostgreSQL runtime；测试只清理自己创建的精确 IDs，不重置开发库。原生 roundtrip 与真实 Twikit Bookmark 验证执行 `LexicalRetrievalManager.maintain` 的实际扫描、投影和检索链，在 HTTP Storage 字节边界断言零调用；同时检查归档不额外创建内容块、blob 或 Job。固定 fixture 同时用于原生 roundtrip 和独立上游合同验证，真实 userscript、HTTP 会话及 Web 消费仍需要各自的纵向验收。
