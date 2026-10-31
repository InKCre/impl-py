# GitHub Extension for InKCre

The GitHub Extension synchronizes the authenticated account's current Stars and GitHub Lists into the InKCre info-base。

## Collected graph

```text
Source --collects--> GitHub Account
GitHub Account --stars--> Repository
GitHub Account --owns--> GitHub List
GitHub List --contains--> Repository
GitHub Account --owns--> Repository
```

Repository、Account and List metadata remain reusable Blocks。When a Star or List membership disappears remotely，ordinary
collection removes the corresponding Relation without deleting those Blocks。

## Configuration

Create a Source of type `extensions.github.stars.Source` with：

```json
{
  "github_token": "<personal access token>"
}
```

The token determines the authenticated account and visible data。The Source does not accept a separate username or private
repository filter。Changing credentials for the same account is supported；credentials that resolve to another account require
a new Source。

## Collection

Dispatch the ordinary `core.source.collect.v1` Job through the generic Source/Job surface。Each run fetches a complete current
Stars and Lists snapshot before reconciling graph facts。There is no extension-specific collection endpoint、incremental
cursor、`full` option or historical backfill mode。

Stars、Lists 和各 List 的 items 都必须完成分页后才进入 graph reconciliation。
如果 GitHub 声明还有下一页却没有提供有效游标，或者游标形成循环，当前任务会失败；
系统不会把这次不完整的观察当成完整快照，也不会据此移除已有关系。
