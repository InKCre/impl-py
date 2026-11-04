"""Mechanics-only HTTP storage returning bounded response bytes."""

from __future__ import annotations

import aiohttp
import pydantic
import sqlmodel

from inkcre_core.utils.base import AIOHTTP_CONNECTOR_GETTER

from .main import Storage


__all__ = [
  "HTTPStorageConfig",
  "HTTPStorage",
  "StorageContentTooLargeError",
]


DEFAULT_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024


class HTTPStorageConfig(sqlmodel.SQLModel):
  """Configuration for bounded HTTP byte retrieval."""

  timeout: int = pydantic.Field(default=30, gt=0)
  follow_redirects: bool = True
  max_response_bytes: int = pydantic.Field(
    default=DEFAULT_MAX_RESPONSE_BYTES,
    gt=0,
  )


class StorageContentTooLargeError(ValueError):
  """The configured byte boundary was exceeded while hydrating content."""


class HTTPStorage(
  Storage[HTTPStorageConfig, bytes],
  stg_type="http",
  config_cls=HTTPStorageConfig,
):
  """Fetch opaque bytes over HTTP(S) without assigning content semantics."""

  @staticmethod
  def _url(block_content: str) -> str:
    url = block_content.strip()
    if not url.lower().startswith(("http://", "https://")):
      raise ValueError("HTTP storage pointer must use http:// or https://")
    return url

  def get_transfer_url(self, block_content: str) -> str:
    return self._url(block_content)

  async def get_raw_content(self, block_content: str) -> bytes:
    url = self._url(block_content)

    timeout = aiohttp.ClientTimeout(total=self._config.timeout)
    async with aiohttp.ClientSession(
      connector=AIOHTTP_CONNECTOR_GETTER(),
      timeout=timeout,
    ) as session:
      async with session.get(
        url,
        allow_redirects=self._config.follow_redirects,
      ) as response:
        response.raise_for_status()
        content_length = response.content_length
        if content_length is not None and content_length > self._config.max_response_bytes:
          raise StorageContentTooLargeError(
            f"HTTP response declares {content_length} bytes; "
            f"limit is {self._config.max_response_bytes}"
          )

        chunks: list[bytes] = []
        received = 0
        async for chunk in response.content.iter_chunked(READ_CHUNK_BYTES):
          received += len(chunk)
          if received > self._config.max_response_bytes:
            raise StorageContentTooLargeError(
              f"HTTP response exceeded {self._config.max_response_bytes} bytes"
            )
          chunks.append(chunk)
        return b"".join(chunks)
