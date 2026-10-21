# Changelog

This changelog records notable changes to this first-party Python Distribution
association. New entries are rendered from project-local fragments by [Towncrier](https://towncrier.readthedocs.io/).

<!-- towncrier release notes start -->

## 0.5.0 - 2026-10-21

### Added

- 支持以现有 JWT 连接 x-monkey，保存与合并用户、帖子和媒体元数据，与 Bookmark 共用原生身份；媒体保持按需读取，会话默认七天并自动续签。


## 0.4.1 - 2026-09-21

### Changed

- 声明兼容 Core Host SDK 0.3，保留 SDK 0.2 支持；不改变插件功能。


## 0.4.0 - 2026-09-19

### Removed

- 采用 Core Host 0.2 异步数据库合同，结束对旧同步 Host 窗口的兼容；发布为新的不可变 Extension 版本。 (#105)


## 0.3.1 - 2026-09-14

### Fixed

- 配置与 state 已由 runtime 恢复时直接使用 typed 值，保留真正的输入边界校验。


## 0.3.0 - 2026-08-26
### Added
* Add the Python Distribution for the 0.3 Extension Release.

## 0.2.1 - 2026-08-25
### Fixed
* Kept OAuth App credentials visible in setup and narrowed Core status to runtime-owned state.

## 0.2.0 - 2026-08-17
### Added
* Added the guided OAuth and Bookmark Source setup wizard.

### Changed
* Limited the Core setup protocol to OAuth and account operations; Bookmark Source scheduling now uses ordinary deployment resources.

## 0.1.1 - 2026-08-17

### Changed

- Recorded the existing published Python Distribution as the Changie baseline.
