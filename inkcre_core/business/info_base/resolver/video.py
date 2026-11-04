"""Exact video resolver using metadata-only PyAV inspection."""

import asyncio
from dataclasses import dataclass
from fractions import Fraction
from io import BytesIO
import typing

import av
import pydantic

from inkcre_core.business.deployment_config import (
  DeploymentConfigManager,
  DeploymentConfigService,
)
from inkcre_core.schemas.ai import VideoContentPart
from inkcre_core.schemas.info_base.block import BlockForm
from inkcre_core.schemas.info_base.main import StarsGraphForm

from .audio import _container_name, _duration_ms, _primary_stream
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
from .materialization import materialize_text_child, try_materialize_model_text


VIDEO_RESOLVER_CONFIG_KEY = "core.resolver.video"
VIDEO_RESOLVER_CONFIG_SCHEMA = "core.resolver.video.config.v1"


class VideoResolverConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  text_model: int
  transcript_model: int


DeploymentConfigManager.register_schema(
  VIDEO_RESOLVER_CONFIG_SCHEMA, VideoResolverConfig, keys=(VIDEO_RESOLVER_CONFIG_KEY,)
)


@dataclass(frozen=True, slots=True)
class VideoSolvedContent(ByteContentFacts):
  container: str | None
  video_codec: str | None
  duration_ms: int | None
  width: int | None
  height: int | None
  frame_rate: float | None
  subtitles: tuple[str, ...]


def _frame_rate(rate: Fraction | None) -> float | None:
  return float(rate) if rate is not None else None


def _subtitles(container: typing.Any) -> tuple[str, ...]:
  values: list[str] = []
  streams = tuple(container.streams.subtitles)
  if not streams:
    return ()
  for packet in container.demux(*streams):
    try:
      decoded = packet.decode()
    except (av.FFmpegError, OSError, ValueError):
      continue
    for subtitle in decoded:
      raw = getattr(subtitle, "dialogue", None) or getattr(subtitle, "text", None)
      if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace").strip()
      elif isinstance(raw, str):
        text = raw.strip()
      else:
        text = ""
      if text and (not values or values[-1] != text):
        values.append(text)
  return tuple(values)


def _inspect_video(content: bytes) -> VideoSolvedContent:
  try:
    with av.open(BytesIO(content), mode="r") as container:
      stream = _primary_stream(container.streams.video)
      if stream is None:
        raise ResolverContentError("core.video.v1", "no video stream")
      codec = stream.codec_context
      facts = VideoSolvedContent(
        content=content,
        byte_size=len(content),
        detected_media_type=detect_media_type(content),
        container=_container_name(container.format.name),
        video_codec=codec.name,
        duration_ms=_duration_ms(container, stream),
        width=codec.width,
        height=codec.height,
        frame_rate=_frame_rate(stream.average_rate),
        subtitles=_subtitles(container),
      )
  except ResolverContentError:
    raise
  except (av.FFmpegError, OSError, ValueError) as error:
    raise ResolverContentError("core.video.v1", "invalid video") from error
  return facts


class VideoResolver(
  Resolver[VideoSolvedContent, bytes],
  rso_type="core.video.v1",
):
  @classmethod
  def create_graph(cls, url: str) -> StarsGraphForm:
    return StarsGraphForm(
      block=BlockForm(resolver=cls.__rsotype__, content=url, storage=-1)
    )

  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> VideoSolvedContent:
    del materialize_missing
    content = require_bytes(
      await self.get_raw_content(refresh=refresh),
      self.__rsotype__,
    )
    return await asyncio.to_thread(_inspect_video, content)

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
      if solved.subtitles:
        await materialize_text_child(
          self.block_id, "subtitle", "\n\n".join(solved.subtitles)
        )
      config = typing.cast(
        VideoResolverConfig | None,
        await DeploymentConfigService.get(VIDEO_RESOLVER_CONFIG_KEY),
      )
      if config is not None:
        media_type = solved.detected_media_type or (
          f"video/{solved.container}" if solved.container is not None else None
        )
        if media_type is not None:
          media = VideoContentPart(
            data=solved.content,
            mime_type=media_type,
            transfer_url=await self.get_transfer_url(),
          )
          await try_materialize_model_text(
            block_id=self.block_id,
            role="transcript",
            model=config.transcript_model,
            instruction=(
              "Transcribe only speech and authored spoken language in the video. "
              "Return plain text with no summary or visual description."
            ),
            media=media,
          )
          await try_materialize_model_text(
            block_id=self.block_id,
            role="text",
            model=config.text_model,
            instruction=(
              "Transcribe only written text visibly present in the video. "
              "Return plain text with no summary or scene description."
            ),
            media=media,
          )
    return format_lexical_facts(
      "video",
      (
        ("media type", solved.detected_media_type),
        ("container", solved.container),
        ("codec", solved.video_codec),
        ("duration ms", solved.duration_ms),
        ("width", solved.width),
        ("height", solved.height),
        ("frame rate", solved.frame_rate),
        ("subtitle cues", len(solved.subtitles) or None),
      ),
    )

  async def get_label(self, *, refresh: bool = False) -> str:
    del refresh
    return "video"
