"""Read persisted text without accessing the original Peer or executing a Source."""

from pathlib import PurePosixPath

from app.business.info_base.resolver import Resolver, TextProjectionContext
from app.business.info_base.resolver.label import format_label
from app.business.info_base.services import BlockService

from .schema import FILE_RESOLVER, FileMetadata, SolvedFile, file_location


class LocalFileResolver(Resolver[SolvedFile, str], rso_type=FILE_RESOLVER):
  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> SolvedFile:
    metadata = FileMetadata.model_validate_json(await self.get_raw_content(refresh=refresh))
    relations = await self.get_relations(
      include_in=False, include_out=True, refresh=refresh
    )
    bodies = [item for item in relations if item.content == "body"]
    if len(bodies) > 1 or (metadata.revision is not None and not bodies):
      raise ValueError("文件资料的正文关系无效")
    if not bodies:
      return SolvedFile(metadata=metadata, body_id=None, text=None)
    block = await BlockService.get(bodies[0].to_)
    if block is None or block.resolver != "core.text.v1" or block.storage is not None:
      raise ValueError("文件正文不存在或类型不符")
    return SolvedFile(metadata=metadata, body_id=block.id, text=block.content)

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    solved = await self._get_solved_content(refresh=refresh, materialize_missing=False)
    metadata = solved.metadata
    parts = [f"原件位置：{metadata.location}"]
    if not metadata.available:
      parts.append("原件已失效；保留最后收入的正文。")
    if metadata.issue:
      parts.append(f"当前提取失败：{metadata.issue}。正文对应上次成功修订。")
    if metadata.revision and metadata.revision.scope == "main_document":
      parts.append("DOCX 主文档文字，不含脚注、批注。")
    if solved.text is not None:
      parts.append(solved.text)
    return "\n\n".join(parts)

  async def get_label(self, *, refresh: bool = False) -> str:
    metadata = FileMetadata.model_validate_json(await self.get_raw_content(refresh=refresh))
    _, path = file_location(metadata.location)
    return format_label("本地文件", PurePosixPath(path).name)
