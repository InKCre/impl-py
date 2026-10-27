# inkcre-cli Changelog

<!-- towncrier release notes start -->

## 0.3.0 - 2026-10-27

### Removed

- Replace `extension install/uninstall` with `extension select --distribution python|module_federation --version VERSION` and `extension unselect`, fixing requests that return 405 against current Core. Selection and unselection accept `--peer`; unselection preserves shared configuration and collected information. Add `extension status` and discover configuration schemas from the connected Peer's status rather than removed shared-identity fields. These commands require Core's Extension management v2 API and do not support the old management interface. (#selection-v2)


## 0.2.1 - 2026-10-21

### Changed

- Rename the package Repository URL from `InKCre/core-py` to `InKCre/impl-py`. (#rename-repository-impl-py)


## 0.2.0 - 2026-09-20

### Added

- Add Sink lifecycle management and asynchronous Agent Query commands.


## 0.1.0 - 2026-09-14

### Added

- 提供独立的 inkcre-cli：经 Core REST 检索与读取信息、动态发现 Resolver 方法和输入 schema、管理 Source/Agent/config/Extension/Cron，并支持 Job 有界观察、停止请求及完整二进制文件交付。
