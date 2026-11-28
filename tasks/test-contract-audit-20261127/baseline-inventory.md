# 全仓基线与执行入口

基点 `9f75cbb`。按 Git 跟踪文件与 JUnit 的实际文件名对账，两列分别是在无数据库、一次性 PostgreSQL 下的通过/失败/跳过数。CLI 使用独立 unittest，三例另行执行。逐声明、参数和保留依据见同目录各责任区账本。

| 基点测试文件 | 责任区 | 声明数 | 默认环境 | PostgreSQL |
| --- | --- | ---: | --- | --- |
| `cli/tests/test_extension.py` | CLI | 3 | 3/0/0 | 独立 CLI |
| `deploy/integrations/chatgpt/tests/test_lifecycle.py` | 交付与开发工具 | 4 | 4/0/0 | 4/0/0 |
| `scripts/tests/caldav/test_codec.py` | CalDAV/Twitter | 2 | 3/0/0 | 3/0/0 |
| `scripts/tests/caldav/test_dav_graph.py` | CalDAV/Twitter | 5 | 0/0/6 | 6/0/0 |
| `scripts/tests/caldav/test_semantic_graph.py` | CalDAV/Twitter | 4 | 0/0/5 | 5/0/0 |
| `scripts/tests/test_extension_documentation.py` | 交付与开发工具 | 1 | 2/0/0 | 2/0/0 |
| `scripts/tests/test_preview_delivery.py` | 交付与开发工具 | 2 | 10/0/0 | 10/0/0 |
| `scripts/tests/twitter/integration/test_archive_graph.py` | CalDAV/Twitter | 9 | 0/0/9 | 7/2/0 |
| `scripts/tests/twitter/integration/test_archive_http_roundtrip.py` | CalDAV/Twitter | 1 | 0/0/1 | 1/0/0 |
| `scripts/tests/twitter/test_archive_contract.py` | CalDAV/Twitter | 14 | 51/0/0 | 51/0/0 |
| `scripts/tests/twitter/test_archive_http.py` | CalDAV/Twitter | 13 | 16/0/0 | 16/0/0 |
| `tests/agent/integration/test_agent_definition.py` | 执行与状态 | 1 | 0/0/1 | 1/0/0 |
| `tests/agent/test_debug_trace.py` | 执行与状态 | 3 | 4/0/0 | 4/0/0 |
| `tests/ai/integration/test_ai_registry.py` | 执行与状态 | 1 | 0/0/1 | 1/0/0 |
| `tests/deployment_config/integration/test_config_resource.py` | 执行与状态 | 2 | 0/0/2 | 2/0/0 |
| `tests/extensions/bilibili/test_bilibili_reader.py` | 来源与附件 | 4 | 15/0/0 | 15/0/0 |
| `tests/extensions/bilibili/test_lexical_integration.py` | 来源与附件 | 1 | 0/0/1 | 1/0/0 |
| `tests/extensions/local_files/test_reader.py` | 来源与附件 | 3 | 4/0/0 | 4/0/0 |
| `tests/extensions/mail/acceptance/test_mail_vertical.py` | 来源与附件 | 1 | 0/0/1 | 0/0/1 |
| `tests/extensions/mail/test_newsletter.py` | 来源与附件 | 10 | 31/0/0 | 31/0/0 |
| `tests/extensions/mail/test_newsletter_graph.py` | 来源与附件 | 1 | 0/0/1 | 0/0/1 |
| `tests/extensions/memos/integration/test_postgresql_attachment_graph.py` | 来源与附件 | 10 | 0/0/16 | 16/0/0 |
| `tests/extensions/rss/integration/test_feed_collection.py` | 来源与附件 | 2 | 0/0/2 | 2/0/0 |
| `tests/extensions/rss/integration/test_live_feed_protocols.py` | 来源与附件 | 1 | 0/0/2 | 0/0/2 |
| `tests/extensions/twitter/test_bookmark_backfill.py` | CalDAV/Twitter | 5 | 7/0/0 | 7/0/0 |
| `tests/info_base/integration/test_graph_commands.py` | 知识与检索 | 8 | 0/0/8 | 8/0/0 |
| `tests/info_base/integration/test_mcp_discovery.py` | 知识与检索 | 1 | 0/0/1 | 1/0/0 |
| `tests/info_base/integration/test_mcp_http_journey.py` | 知识与检索 | 1 | 0/0/1 | 1/0/0 |
| `tests/info_base/test_markdown.py` | 知识与检索 | 1 | 1/0/0 | 1/0/0 |
| `tests/lexical_retrieval/acceptance/test_multimodal_live.py` | 知识与检索 | 1 | 0/0/1 | 0/0/1 |
| `tests/lexical_retrieval/integration/test_lexical_retrieval.py` | 知识与检索 | 3 | 0/0/3 | 3/0/0 |
| `tests/migrations/test_extension_peer_enabled_rpc.py` | 执行与状态 | 6 | 0/0/6 | 0/0/6 |
| `tests/migrations/test_memos_attachment_v2_migration.py` | 执行与状态 | 2 | 0/0/2 | 2/0/0 |
| `tests/organization/acceptance/test_black_box.py` | 知识与检索 | 1 | 0/0/1 | 0/0/1 |
| `tests/organization/integration/test_behavior_graph.py` | 知识与检索 | 3 | 0/0/3 | 3/0/0 |
| `tests/organization/integration/test_media_interpretation.py` | 知识与检索 | 1 | 0/0/1 | 1/0/0 |
| `tests/organization/integration/test_rumination_graph.py` | 知识与检索 | 2 | 0/0/2 | 2/0/0 |
| `tests/peer/integration/test_peer_runtime.py` | 执行与状态 | 2 | 0/0/2 | 2/0/0 |
| `tests/semantic_retrieval/acceptance/test_vertical_quality.py` | 知识与检索 | 4 | 0/0/4 | 0/3/1 |
| `tests/semantic_retrieval/integration/test_semantic_retrieval.py` | 知识与检索 | 1 | 0/0/1 | 1/0/0 |
| `tests/test_core_context.py` | 执行与状态 | 7 | 7/0/0 | 7/0/0 |
| `tests/test_dev_database_provider.py` | 交付与开发工具 | 2 | 2/0/0 | 2/0/0 |
| `tests/test_extension_distribution.py` | 执行与状态 | 1 | 1/0/0 | 1/0/0 |
| `tests/test_extension_participation_lifecycle_races.py` | 执行与状态 | 1 | 2/0/0 | 2/0/0 |
| `tests/test_extension_peer_status.py` | 执行与状态 | 2 | 3/0/0 | 3/0/0 |
| `tests/test_extension_publication_recovery.py` | 交付与开发工具 | 1 | 1/0/0 | 1/0/0 |
| `tests/test_extension_registry_config.py` | 执行与状态 | 6 | 10/0/0 | 10/0/0 |
| `tests/test_extension_startup_provision.py` | 执行与状态 | 8 | 10/0/0 | 10/0/0 |
| `tests/test_mcp_recall.py` | 知识与检索 | 1 | 1/0/0 | 1/0/0 |
| `tests/test_observability_records.py` | 执行与状态 | 10 | 10/0/0 | 10/0/0 |
| `tests/test_production_delivery_app_resolution.py` | 交付与开发工具 | 3 | 4/0/0 | 4/0/0 |
| `tests/test_production_image_transfer.py` | 交付与开发工具 | 2 | 2/0/0 | 2/0/0 |
| `tests/test_production_maintenance.py` | 交付与开发工具 | 3 | 3/0/0 | 3/0/0 |
| `tests/test_production_preflight.py` | 交付与开发工具 | 11 | 16/0/1 | 17/0/0 |
| `tests/test_production_stopped_apps.py` | 交付与开发工具 | 2 | 2/0/0 | 2/0/0 |
| `tests/test_self_host_database_confirmation.py` | 交付与开发工具 | 2 | 2/0/0 | 2/0/0 |

基线之外另纳入上轮新增的 CLI、Job/Cron、Peer HTTP、观测重开和图路径模块，以及本轮来源和协议适配场景。各自的责任与失败条件见六份账本。

其他验收入口包括 CI 静态检查、wheel 构建及内容检查、镜像重复初始化、PostgREST、reset、Web 就绪、导出恢复和 Preview，以及文档列出的 CLI 命令、真实提供方和设备场景。历史 tasks 中的异步数据库、观测实验和组织研究探针属于已有任务证据，正式工作流和发行命令不调用这些探针。
