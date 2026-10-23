# 本地文件读取分发

`extensions/local_files` 交付 `inkcre/local-files` 的 Python 读取能力，解释 `extensions.local_files.file.v1` 与 `body` 关系。源合同在 Hub 的 `20-product-tdd/local-files-contract.md`。目录监视、文件提取及共享写入由具备文件权限的执行分发完成；Python 分发不提供收入 API，也不注册目录 Source 占位类。

`LocalFileResolver` 读取元数据并校验唯一的 `core.text.v1` 正文，允许首次失败时尚无正文。原件失效或当前提取失败仍返回最后成功正文，同时保留状态和提取范围。位置只作为文字解释，不访问本机路径，不尝试远端取文件，也不物化其他副本。此功能复用已有 Source／Job／图模型，没有新增迁移。
