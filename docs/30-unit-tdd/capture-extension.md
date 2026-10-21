# Capture

`inkcre/capture` 保存一次主动采集的原材料、真实可得的出处与用户附注。原生 Mac Peer 负责系统交互和本机待办，Python 分发执行提交并解释图，TypeScript 分发在 Web Peer 中解释同一份持久内容。Mac 不是扩展宿主；停止 Mac 或原执行 Peer 不影响其他已安装读取分发的 Peer 读取共享图和原始图片。

此文拥有 Capture 这个具体扩展的业务合同及 Python 实现边界；请求字段由 `extensions/capture/schema.py` 定义，Swift 与 Python 共用 `tests/extensions/capture/fixtures/capture-v1.json` 指纹样例。数据库结构和迁移由本仓库拥有。Mac 的 SQLite、权限与安装说明归 `InKCre/impl-swift/README.md`，Web 分发归 `InKCre/impl-ts/extensions/capture`。

## 提交与恢复

已启用的 Python 分发公告精确能力 `extensions.capture.submit.v1`，通过既有 Peer HTTP 协议接收 `POST /capture/submit`。调用者使用部署 Peer JWT；这不是匿名写入入口。执行分发要求 Python Host SDK `>=0.3.4 <0.4.0`，读取分发要求 TypeScript Host SDK `>=0.4.0 <0.5.0`。两者独立安装和启用。

请求包含 UUID `submission_id`、带时区的 `captured_at`、入口 `selection | paste | screenshot | manual`、可缺失的 `origin`、有序 `materials` 和可缺失的 `annotation`。出处的 `application_id`、`title`、`url` 都允许缺失；显式粘贴不能推断原始来源。文本材料使用 `kind: text` 和 `text`，图片使用 `kind: image`、`mime_type`、`data_base64` 和可缺失的 `filename`。材料与附注不能同时为空。

单次最多 128 份材料，文本和附注的 UTF-8 字节数加解码图片累计不得超过 32 MiB，HTTP JSON 请求不得超过 48 MiB。支持 PNG、JPEG、GIF、WebP；执行方验证 Base64、实际图片格式与声明的 MIME，并拒绝解码尺寸过大的图片。原始字节不经过缩放或重新编码。代理也必须允许相同请求体上限；这两个上限是协议边界，不是所有设备与代理都已完成性能校准的承诺。

调用方先冻结提交 UUID、材料和目标信息库，再发送。网络超时不证明提交失败。恢复先查询同一信息库的 `capture_receipts`，核对指纹，再确认本机结果；没有回执时只可使用原 UUID 和原材料重试。切换当前连接不能重定向已有待办。再次主动采集相同内容使用新 UUID，不进行内容去重。

指纹为规范 JSON 的 SHA-256：对象键排序、紧凑分隔符、UTF-8、缺失的可选字段显式为 `null`，时间转 UTC 并保留六位小数，图片的 `data_base64` 替换为原始字节的 `data_sha256`。UUID 使用标准小写形式；数组顺序、文本换行及 Unicode 组合形式保持原样。Swift 对冻结载荷的网络时间表示再规范化，避免本机亚毫秒时间参与指纹却未被发送。

相同 UUID、相同指纹返回原回执；相同 UUID、不同指纹返回 409，客户端保留材料并要求处理。成功结果包含 `submission_id`、`root_block_id`、`committed_at` 和 `availability: available | deleted`。根 Block 删除后回执仍保留，重试返回 `deleted`，不能复活内容。

## 持久图

| 对象或关系 | 合同 |
| --- | --- |
| 根 Block | `extensions.capture.capture.v1`，内容为时间、入口、出处的 JSON |
| 根 → 材料 | `material:0` 起连续递增的关系，序号不可重复 |
| 文本材料 | 标准 `core.text.v1`，原文保持不变 |
| 图片材料 | `extensions.capture.attachment.v1`，内容为 MIME 与可选文件名 |
| 图片材料 → 原始内容 | 唯一 `content` 关系，目标为 `core.image.v1` |
| 原始图片 | 标准 PostgreSQL binary storage（`storage = -4`）保存原始字节 |
| 根 → 用户附注 | 至多一条 `annotation` 关系，目标为 `core.text.v1` |

Resolver 按关系解释材料顺序，不把附注混入原文；缺失或畸形关系显式报错。Web 来源链接仅允许 HTTP(S) 点击，其他来源位置仍可作为文本查看。图片通过标准 storage/resolver 读取，不依赖 Mac 本地文件、Python 内存或派生 OCR。OCR、通用文件、整页抓取和知识查询不属于此分发的提交合同。

## 原子性与验证

`capture_uow` 将现有图持久化、binary storage bytes 与回执放入同一个 PostgreSQL 事务。UUID 派生的事务 advisory lock 串行化同一提交的竞争者；回执主键是最后一道唯一性约束。回执中的根 ID 不设删除级联外键，防止根删除后丢失已处理身份。通用 `submit_graph` 仍保持每次新建图的原有语义。

验证代码覆盖同身份并发、不同载荷冲突、根删除后的重放、新 UUID 的相同内容、真实数据库约束失败后的完整回滚、请求验证不回显私密材料，以及 Swift/Python 规范指纹一致性。数据库测试只在显式指定的、已迁移的可丢弃 PostgreSQL 上运行。真实 Mac 队列还以提交后丢响应代理验证了重开 SQLite 后通过共享回执恢复结果。
