"""Validate one capture intention and atomically persist its result exactly once."""

import asyncio
import base64
import binascii
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import warnings

from PIL import Image, UnidentifiedImageError

from app.business.info_base.commands import persist_graph
from app.business.info_base.storage import StorageManager, WritableStorage
from app.persistence.capture import capture_uow
from app.schemas.capture import CaptureResult
from app.schemas.info_base.main import GraphBlockForm, GraphForm, GraphRelationForm

from .schema import (
  ATTACHMENT_RESOLVER,
  CAPTURE_RESOLVER,
  MAX_MATERIAL_BYTES,
  AttachmentMetadata,
  CaptureMetadata,
  CaptureRequest,
  ImageMaterial,
  TextMaterial,
)


class CaptureConflict(ValueError):
  pass


class InvalidCapture(ValueError):
  pass


@dataclass(frozen=True)
class PreparedCapture:
  digest: str
  images: dict[int, bytes]


def prepare_capture(request: CaptureRequest) -> PreparedCapture:
  """Normalize only protocol representations, never authored text or material order."""
  if not request.materials and not request.annotation:
    raise InvalidCapture("至少需要一份材料或附注")
  document = request.model_dump(mode="json")
  document["captured_at"] = request.captured_at.isoformat(timespec="microseconds")
  materials = []
  images: dict[int, bytes] = {}
  size = len((request.annotation or "").encode("utf-8"))
  for index, material in enumerate(request.materials):
    item = material.model_dump(mode="json")
    if isinstance(material, TextMaterial):
      size += len(material.text.encode("utf-8"))
    else:
      try:
        content = base64.b64decode(material.data_base64, validate=True)
      except (ValueError, binascii.Error) as error:
        raise InvalidCapture("图片必须使用有效的 Base64 编码") from error
      size += len(content)
      if size > MAX_MATERIAL_BYTES:
        raise InvalidCapture("材料累计超过 32 MiB")
      try:
        with warnings.catch_warnings():
          warnings.simplefilter("error", Image.DecompressionBombWarning)
          with Image.open(BytesIO(content)) as image:
            if Image.MIME.get(image.format or "") != material.mime_type:
              raise InvalidCapture("图片内容与声明的 MIME 不一致")
            image.verify()
      except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
      ) as error:
        raise InvalidCapture("图片格式无效或解码尺寸过大") from error
      images[index] = content
      del item["data_base64"]
      item["data_sha256"] = sha256(content).hexdigest()
    if size > MAX_MATERIAL_BYTES:
      raise InvalidCapture("材料累计超过 32 MiB")
    materials.append(item)
  if size > MAX_MATERIAL_BYTES:
    raise InvalidCapture("材料累计超过 32 MiB")
  document["materials"] = materials
  digest = sha256(
    json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
      "utf-8"
    )
  ).hexdigest()
  return PreparedCapture(digest, images)


async def submit_capture(request: CaptureRequest) -> CaptureResult:
  prepared = await asyncio.to_thread(prepare_capture, request)
  async with capture_uow() as uow:
    await uow.receipts.lock(request.submission_id)
    receipt = await uow.receipts.get(request.submission_id)
    if receipt is not None:
      if receipt.payload_digest != prepared.digest:
        raise CaptureConflict("提交身份已用于不同内容；请保留原提交并检查待办")
      exists = await uow.graph.blocks.get(receipt.root_block_id)
      return CaptureResult(
        submission_id=receipt.submission_id,
        root_block_id=receipt.root_block_id,
        committed_at=receipt.committed_at,
        availability="available" if exists is not None else "deleted",
      )

    metadata = CaptureMetadata(
      captured_at=request.captured_at, entry=request.entry, origin=request.origin
    )
    blocks = [
      GraphBlockForm(id=-1, resolver=CAPTURE_RESOLVER, content=metadata.model_dump_json())
    ]
    relations: list[GraphRelationForm] = []
    for index, material in enumerate(request.materials):
      local_id = -(len(blocks) + 1)
      if isinstance(material, ImageMaterial):
        storage = StorageManager.from_record(await uow.graph.storage.get(-4))
        if not isinstance(storage, WritableStorage):
          raise TypeError("Capture requires writable PostgreSQL binary storage")
        pointer = await storage.create_content(prepared.images[index], uow.graph.storage)
        attachment = AttachmentMetadata(
          mime_type=material.mime_type, filename=material.filename
        )
        blocks.extend(
          (
            GraphBlockForm(
              id=local_id,
              resolver=ATTACHMENT_RESOLVER,
              content=attachment.model_dump_json(),
            ),
            GraphBlockForm(
              id=local_id - 1,
              resolver="core.image.v1",
              content=pointer,
              storage=-4,
            ),
          )
        )
        relations.append(
          GraphRelationForm(from_=local_id, to_=local_id - 1, content="content")
        )
      else:
        blocks.append(
          GraphBlockForm(id=local_id, resolver="core.text.v1", content=material.text)
        )
      relations.append(
        GraphRelationForm(from_=-1, to_=local_id, content=f"material:{index}")
      )
    if request.annotation:
      local_id = -(len(blocks) + 1)
      blocks.append(
        GraphBlockForm(id=local_id, resolver="core.text.v1", content=request.annotation)
      )
      relations.append(GraphRelationForm(from_=-1, to_=local_id, content="annotation"))
    result = await persist_graph(
      GraphForm(blocks=tuple(blocks), relations=tuple(relations)), uow.graph
    )
    root_id = next(item.id for item in result.blocks if item.local_id == -1)
    receipt = await uow.receipts.create(request.submission_id, prepared.digest, root_id)
    response = CaptureResult(
      submission_id=receipt.submission_id,
      root_block_id=receipt.root_block_id,
      committed_at=receipt.committed_at,
      availability="available",
    )
  return response
