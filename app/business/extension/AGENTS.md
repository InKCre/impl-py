# extension/ Local Guide

本目录实现唯一的 Core Extension Host。修改前同时核对 `main.py`、`state.py`、
`release.py`、`distribution.py`、`app/schemas/extension/main.py` 与 `run.py`。

## 稳定边界

- `extensions` 表示共享的 Extension identity/config/state；
  `extension_peer_participations(name, peer_id)` 保存每个 Peer 的精确分发、版本、enabled intent 与该版本的 config schema。
  Selection、enabled intent、loaded version、running 必须分别报告。
- `ExtensionBase` 保持配置校验、`on_start_async`、`on_close` 与 active-effect publication。Route、Peer inbound
  和 public claim 可逆；已 import 的 Source/Resolver/Sink class registration 在当前进程内单调保留。
- Extension wheel 直接 import Core 模块；Host 只接受标准 `inkcre.core.extensions` entry point。
- `ActiveExtensionEffects` 拥有已取得的 claim/class 效果及释放顺序；新增 active effect 只修改该所有者。
  普通停止遇错立即退出；intent 冲突清理聚合 close/unpublish/unbind 错误；启动失败保留原错误并尽力
  收尾。claim 释放错误不被吞掉。环境应用由 Host 在已持锁处关闭 Extension，bootstrap callback 只排空生产者。
- Registry Simple URL 必须与配置的 Registry 同源且路径精确匹配 Project。
- runtime 只能把普通 wheel 安装到当前 Core interpreter/site-packages；禁止 `pip --target`、
  per-Extension overlay、`sys.path` 或 `extensions.__path__` 改写。
- Registry admission、已安装记录复用与 Runtime SDK 的实际依赖安装行为，以
  [First-Party Extension Distribution](../../../docs/40-deployment/first-party-extension-distribution.md)
  为唯一说明。不要从 Extension wheel 的下载参数推导依赖安装策略。
- 只在该 Peer 已 enabled 时拒绝替换它自己的 selection，其他 Peer 不锁住该 Peer 的版本。已 import 的 Project
  被替换后必须重启，当前进程不可热加载新 class；disable 也不模拟卸载 Python module。
- 新 install/upgrade 只接受 published；已安装 exact yanked Release 可 enable/cold restore 并告警。
- 选择缺失的 Python wheel 时先下载并验证；暂停 Peer、排空任务并关闭 Extension 后，才把 wheel 安装到共享解释器。
  应用失败时保留暂停与明确错误，不声称回滚。应用完成后必须以新进程重载 Python 模块。
- enable 只允许启动当前 Peer 已选中的 exact installed Distribution，不隐式安装 pip 包。disable 先停止 runtime
  再保存 intent；冷启动只恢复该 Peer 的精确选择。
- 未解析的旧 enabled member 保留原版本与 enabled intent，必须精确解析该版本；失败不得删 intent，也不得回退到 latest。
- `ExtensionBase` 向 Extension 提供 fresh validated config 读写与 typed deployment-wide state
  mutation；Extension 不接触 SQLModel，数据库行锁与并发语义仍由 Core store 实现。
- Extension-specific setup 通过 running Extension 发布的 typed Peer inbound 实现；Host 不提供
  generic setup/wizard protocol。公开 OAuth callback 必须是 lifecycle-bound exact route claim。
- Registry origin 每次 operation 按 executing Peer override、deployment config、process fallback
  解析一次，并由 exact Release 与 Distribution consumer 共用该 snapshot。

## 权限和持久化

`state.py` 拥有异步持久化用例与 shared state/config 规则；SQL 和 session-bound repository 位于
`app/persistence/extension/`，UoW 工厂使用原生异步事务。`extensions.state` 是 deployment-wide Extension-produced state；
`extension_peer_participations` 保存 Peer-local selection、enabled intent 与精确版本 config schema。SQLModel 不应泄露成 Host 的稳定接口。

Extension authoring 与 wheel metadata 规则见 `extensions/AGENTS.md`。
