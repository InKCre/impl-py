"""Exact image resolver using bounded Pillow header inspection."""

import asyncio
from dataclasses import dataclass
from io import BytesIO
import typing

from PIL import Image, UnidentifiedImageError
import pydantic

from inkcre_core.business.deployment_config import (
  DeploymentConfigManager,
  DeploymentConfigService,
)
from inkcre_core.schemas.ai import ImageContentPart
from inkcre_core.schemas.info_base.block import BlockForm
from inkcre_core.schemas.info_base.main import OutArcForm, StarsGraphForm
from inkcre_core.schemas.info_base.relation import RelationForm

from .contracts import (
  ResolverContentError,
  TextProjectionContext,
  UnsupportedResolverCapability,
)
from .inspection import (
  ByteContentFacts,
  detect_media_type,
  format_lexical_facts,
  require_bytes,
)
from .main import Resolver
from .materialization import try_materialize_model_text


IMAGE_RESOLVER_CONFIG_KEY = "core.resolver.image"
IMAGE_RESOLVER_CONFIG_SCHEMA = "core.resolver.image.config.v1"


class ImageResolverConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  text_model: int


DeploymentConfigManager.register_schema(
  IMAGE_RESOLVER_CONFIG_SCHEMA, ImageResolverConfig, keys=(IMAGE_RESOLVER_CONFIG_KEY,)
)


@dataclass(frozen=True, slots=True)
class ImageSolvedContent(ByteContentFacts):
  format: str | None
  width: int | None
  height: int | None
  frame_count: int | None


def _inspect_image(content: bytes) -> ImageSolvedContent:
  try:
    with Image.open(BytesIO(content)) as image:
      image_format = image.format.lower() if image.format else None
      width, height = image.size
      frame_count = getattr(image, "n_frames", None)
      image.verify()
  except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
    raise ResolverContentError("core.image.v1", "invalid image") from error
  return ImageSolvedContent(
    content=content,
    byte_size=len(content),
    detected_media_type=detect_media_type(content),
    format=image_format,
    width=width,
    height=height,
    frame_count=frame_count,
  )


class ImageResolver(
  Resolver[ImageSolvedContent, bytes],
  rso_type="core.image.v1",
):
  @classmethod
  def create_graph(cls, url: str, alt_text: str | None = None) -> StarsGraphForm:
    from .text import TextResolver

    return StarsGraphForm(
      block=BlockForm(resolver=cls.__rsotype__, content=url, storage=-1),
      out_arcs=(
        OutArcForm(
          relation=RelationForm(content="alt:text"),
          to_graph=TextResolver.create_graph(alt_text),
        ),
      )
      if alt_text
      else (),
    )

  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> ImageSolvedContent:
    del materialize_missing
    content = require_bytes(
      await self.get_raw_content(refresh=refresh),
      self.__rsotype__,
    )
    return await asyncio.to_thread(_inspect_image, content)

  async def get_text(
    self,
    *,
    context: TextProjectionContext = "default",
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> str:
    if context == "default":
      raise UnsupportedResolverCapability(self.__rsotype__, "text")
    solved = await self.get_solved_content(refresh=refresh, materialize_missing=False)
    if materialize_missing:
      config = typing.cast(
        ImageResolverConfig | None,
        await DeploymentConfigService.get(IMAGE_RESOLVER_CONFIG_KEY),
      )
      if config is not None:
        media_type = solved.detected_media_type or (
          f"image/{solved.format}" if solved.format is not None else None
        )
        if media_type is not None:
          await try_materialize_model_text(
            block_id=self.block_id,
            role="text",
            model=config.text_model,
            instruction=(
              "Transcribe only written text visibly present in the image. "
              "Return plain text with no explanation; return no content when none exists."
            ),
            media=ImageContentPart(
              data=solved.content,
              mime_type=media_type,
              transfer_url=await self.get_transfer_url(),
            ),
          )
    return format_lexical_facts(
      "image",
      (
        ("media type", solved.detected_media_type),
        ("format", solved.format),
        ("width", solved.width),
        ("height", solved.height),
        ("frames", solved.frame_count),
      ),
    )

  async def get_label(self, *, refresh: bool = False) -> str:
    del refresh
    return "image"
