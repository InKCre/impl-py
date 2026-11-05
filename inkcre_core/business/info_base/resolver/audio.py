"""Exact audio resolver using metadata-only PyAV inspection."""

import asyncio
from dataclasses import dataclass
from io import BytesIO
import typing

import av
from av.stream import Disposition
import pydantic

from inkcre_core.business.deployment_config import (
  DeploymentConfigManager,
  DeploymentConfigService,
)
from inkcre_core.schemas.ai import AudioContentPart

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


AUDIO_RESOLVER_CONFIG_KEY = "core.resolver.audio"
AUDIO_RESOLVER_CONFIG_SCHEMA = "core.resolver.audio.config.v1"


class AudioResolverConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  transcript_model: int


DeploymentConfigManager.register_schema(
  AUDIO_RESOLVER_CONFIG_SCHEMA, AudioResolverConfig, keys=(AUDIO_RESOLVER_CONFIG_KEY,)
)


@dataclass(frozen=True, slots=True)
class AudioSolvedContent(ByteContentFacts):
  container: str | None
  codec: str | None
  duration_ms: int | None
  channels: int | None
  sample_rate_hz: int | None
  bitrate_bps: int | None


def _container_name(format_name: str | None) -> str | None:
  if not format_name:
    return None
  names = format_name.split(",")
  return "mp4" if "mp4" in names else names[0]


def _duration_ms(container, stream) -> int | None:
  if stream.duration is not None and stream.time_base is not None:
    return round(float(stream.duration * stream.time_base) * 1000)
  if container.duration is not None:
    return round(container.duration / 1000)
  return None


def _primary_stream(streams):
  default = [stream for stream in streams if bool(stream.disposition & Disposition.default)]
  ordinary = [
    stream for stream in streams if not bool(stream.disposition & Disposition.attached_pic)
  ]
  candidates = default or ordinary or list(streams)
  return candidates[0] if candidates else None


def _inspect_audio(content: bytes) -> AudioSolvedContent:
  try:
    with av.open(BytesIO(content), mode="r") as container:
      stream = _primary_stream(container.streams.audio)
      if stream is None:
        raise ResolverContentError("core.audio.v1", "no audio stream")
      codec = stream.codec_context
      facts = AudioSolvedContent(
        content=content,
        byte_size=len(content),
        detected_media_type=detect_media_type(content),
        container=_container_name(container.format.name),
        codec=codec.name,
        duration_ms=_duration_ms(container, stream),
        channels=codec.channels,
        sample_rate_hz=codec.sample_rate,
        bitrate_bps=stream.bit_rate or container.bit_rate,
      )
  except ResolverContentError:
    raise
  except (av.FFmpegError, OSError, ValueError) as error:
    raise ResolverContentError("core.audio.v1", "invalid audio") from error
  return facts


class AudioResolver(
  Resolver[AudioSolvedContent, bytes],
  rso_type="core.audio.v1",
):
  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> AudioSolvedContent:
    del materialize_missing
    content = require_bytes(
      await self.get_raw_content(refresh=refresh),
      self.__rsotype__,
    )
    return await asyncio.to_thread(_inspect_audio, content)

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
        AudioResolverConfig | None,
        await DeploymentConfigService.get(AUDIO_RESOLVER_CONFIG_KEY),
      )
      if config is not None:
        media_type = solved.detected_media_type or (
          f"audio/{solved.container}" if solved.container is not None else None
        )
        if media_type is not None:
          await try_materialize_model_text(
            block_id=self.block_id,
            role="transcript",
            model=config.transcript_model,
            instruction=(
              "Transcribe only speech and authored spoken language in the audio. "
              "Return plain text with no summary or explanation."
            ),
            media=AudioContentPart(
              data=solved.content,
              mime_type=media_type,
              transfer_url=await self.get_transfer_url(),
            ),
          )
    return format_lexical_facts(
      "audio",
      (
        ("media type", solved.detected_media_type),
        ("container", solved.container),
        ("codec", solved.codec),
        ("duration ms", solved.duration_ms),
        ("channels", solved.channels),
        ("sample rate Hz", solved.sample_rate_hz),
        ("bitrate bps", solved.bitrate_bps),
      ),
    )

  async def get_label(self, *, refresh: bool = False) -> str:
    del refresh
    return "audio"
