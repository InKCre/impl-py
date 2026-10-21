"""Interpret source roles from the shared graph without depending on the capture Peer."""

import re

from app.business.info_base.resolver import Resolver, ResolverManager, TextProjectionContext
from app.business.info_base.resolver.label import format_label
from app.business.info_base.services import BlockService

from .schema import (
  ATTACHMENT_RESOLVER,
  CAPTURE_RESOLVER,
  AttachmentMetadata,
  CaptureMetadata,
  SolvedAttachment,
  SolvedCapture,
  SolvedMaterial,
)


class AttachmentResolver(Resolver[SolvedAttachment, str], rso_type=ATTACHMENT_RESOLVER):
  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> SolvedAttachment:
    metadata = AttachmentMetadata.model_validate_json(
      await self.get_raw_content(refresh=refresh)
    )
    relations = await self.get_relations(
      include_in=False, include_out=True, refresh=refresh
    )
    links = [relation for relation in relations if relation.content == "content"]
    if len(links) != 1:
      raise ValueError("采集附件必须包含一条 content 关系")
    block = await BlockService.get(links[0].to_)
    if block is None or block.resolver != "core.image.v1":
      raise ValueError("采集附件的图片内容不存在或类型不符")
    return SolvedAttachment(metadata=metadata, content_block_id=links[0].to_)

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    solved = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    block = await BlockService.get(solved.content_block_id)
    if block is None:
      raise ValueError("采集图片已不存在")
    content = await ResolverManager.get(block).get_text(
      context=context, refresh=refresh, materialize_missing=materialize_missing
    )
    return "\n".join(filter(None, (solved.metadata.filename, content)))

  async def get_label(self, *, refresh: bool = False) -> str:
    solved = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    return format_label("采集图片", solved.metadata.filename or solved.metadata.mime_type)


class CaptureResolver(Resolver[SolvedCapture, str], rso_type=CAPTURE_RESOLVER):
  async def _get_solved_content(
    self, *, refresh: bool = False, materialize_missing: bool = True
  ) -> SolvedCapture:
    metadata = CaptureMetadata.model_validate_json(
      await self.get_raw_content(refresh=refresh)
    )
    relations = await self.get_relations(
      include_in=False, include_out=True, refresh=refresh
    )
    material_ids: dict[int, int] = {}
    annotations: list[int] = []
    for relation in relations:
      match = re.fullmatch(r"material:(0|[1-9][0-9]*)", relation.content)
      if match:
        index = int(match[1])
        if index in material_ids:
          raise ValueError("采集材料存在重复序号")
        material_ids[index] = relation.to_
      elif relation.content.startswith("material:"):
        raise ValueError("采集材料序号无效")
      elif relation.content == "annotation":
        annotations.append(relation.to_)
    if sorted(material_ids) != list(range(len(material_ids))) or len(annotations) > 1:
      raise ValueError("采集材料顺序或附注关系无效")
    materials = []
    for index in range(len(material_ids)):
      block = await BlockService.get(material_ids[index])
      if block is None or block.resolver not in ("core.text.v1", ATTACHMENT_RESOLVER):
        raise ValueError("采集材料已不存在或类型不符")
      materials.append(
        SolvedMaterial(block_id=material_ids[index], resolver=block.resolver)
      )
    if annotations:
      block = await BlockService.get(annotations[0])
      if block is None or block.resolver != "core.text.v1":
        raise ValueError("采集附注已不存在或类型不符")
    return SolvedCapture(
      metadata=metadata,
      materials=tuple(materials),
      annotation_block_id=annotations[0] if annotations else None,
    )

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    solved = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    origin = solved.metadata.origin
    parts = [f"采集时间：{solved.metadata.captured_at.isoformat()}"]
    if origin:
      parts.extend(
        f"{label}：{value}"
        for label, value in (
          ("来源应用", origin.application_id),
          ("来源标题", origin.title),
          ("来源地址", origin.url),
        )
        if value
      )
    items = [(item.block_id, "材料") for item in solved.materials]
    if solved.annotation_block_id is not None:
      items.append((solved.annotation_block_id, "用户附注"))
    for block_id, label in items:
      block = await BlockService.get(block_id)
      if block is None:
        raise ValueError("采集内容已不存在")
      text = await ResolverManager.get(block).get_text(
        context=context, refresh=refresh, materialize_missing=materialize_missing
      )
      parts.append(f"{label}：\n{text}")
    return "\n\n".join(parts)

  async def get_label(self, *, refresh: bool = False) -> str:
    metadata = CaptureMetadata.model_validate_json(
      await self.get_raw_content(refresh=refresh)
    )
    return format_label(
      "随手收集",
      (metadata.origin.title if metadata.origin else None) or metadata.entry,
    )
