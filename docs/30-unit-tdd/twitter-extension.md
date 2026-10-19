# Python Twitter 采集与归档

Python Twitter distribution 把 x-monkey 提交和 Bookmark 采集写入同一组普通 Blocks。原生字段与跨分发关系以 [Twitter 归档契约](../_shared/20-product-tdd/twitter-archive-contract.md) 为准；`archive_schema.py` 校验 native wire，`reconcile.py` 拥有本实现的身份选择、事务合并和关系维护。数据库没有另一套归档表。

一次 submit 使用一个 `graph_uow()`。Bookmark 先完成 provider 请求，再进入 `source_uow()`，锁定 Source，使用其 `graph` 合并新主帖及实际捕获的上下文，并在同一事务内推进 cursor。主帖顺序与展开上下文分开，因此引用帖不会成为下一次书签采集的 watermark；旧的数值 cursor 在比较边界转换为十进制字符串，不重置历史状态。

两条写入路径都先取得 `extensions.twitter.reconcile` 全局事务 advisory 锁，再批量重读原生身份。根内容和受管关系一起提交；异常让整批回滚，调用方可安全重放。该锁仅约束正常 Twitter 写入入口，不能替代所有 info-base 原始写入的约束。provider 和媒体网络访问都不能放进此事务。

匹配先选新版 resolver，再按 Block ID 稳定选择旧 `tweet.v1`。旧根重新采集后原地更新，保留现有 Block ID、引用和旧附件关系；未重新采集的旧根仍由 v1 decoder 读取。旧加工正文不足以重建 native RichText，不能用它伪造新记录。Bookmark provider 适配保留实际源正文；共同合并在已有 native 正文时不让较少详情的 Bookmark 正文替换它，其他可证明字段仍可补充。x-monkey 提交继续使用原生稀疏合并语义。

身份引用严格保存为 `{id}`，不补作者、时间或正文。query 对完整 native 记录返回 `found`，身份引用返回 `missing`，能按 ID 定位但结构损坏的记录返回 `failed`。批量身份查询使用有效 JSON 的条件表达式隔离普通文本和损坏 JSON；无法解析且无法定位 ID 的内容不会命中某个原生 ID，不会导致其他正常记录的查询失败。

原生观察时间保存输入字符串，不在序列化时改写精度。逐计数比较遵守上游的毫秒时间比较规则；较旧观察不覆盖新值，相同值的新时间也更新持久记录，无时间的新值删除该计数的旧时间标签。`unchanged` 表示没有持久变化，因此仅观察时间更新仍为 `accepted`。

新版引用边只维护约定的 reference schema，旧 `attachment:*`、`entities:url` 和用户关系保留。媒体引用顺序是 `post.mediaIds`、文章封面、`article.mediaIds` 依次合并去重，首次出现保留。Media 根通过 `content` 指向普通图片或视频内容 Block；关系改变不删除仍可能被别处引用的内容 Block。

媒体原生 URL、variants 和 alt 留在元数据中。只有 HTTPS 的 `pbs.twimg.com`、`video.twimg.com` 地址能够形成 HTTP Storage 指针；视频和 GIF 选择已捕获的最高 bitrate MP4，HLS 仅保留元数据。没有合适地址时仍保存原生事实，但不制造可读取的内容指针。保存、query、文本投影和索引不展开媒体；用户读取内容 Block 时才由现有 HTTP Storage 应用超时和体积限制。本版本不保存媒体本体，也不创建下载 Job，外部来源失效后仍可读取文字与元数据。

Python 的 v2 Post 文本投影包含原帖 ID、链接、已捕获作者、正文和长文；User/Media 投影使用自身已经捕获的文字。label 保持 Block-local，不为投影或标签下载媒体。检索沿用现有独立索引维护流程，不在提交中隐式维护索引。

数据回归入口为 `pdm run python -m pytest -q tests/extensions/twitter`。真实数据库场景要求显式提供 `INKCRE_TEST_DATABASE_URL`，使用本任务独占、已迁移的可丢弃 PostgreSQL runtime；测试只清理自己创建的精确 IDs，不重置开发库。固定 fixture 同时用于原生 roundtrip 和独立上游合同验证，真实 userscript、HTTP 会话及 Web 消费仍需要各自的纵向验收。
