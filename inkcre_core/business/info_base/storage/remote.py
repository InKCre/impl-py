"""Byte lifecycle for writable HTTP-backed storage with storage-relative object IDs."""

from abc import abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import ClassVar
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp
import pydantic
import sqlmodel
from yarl import URL

from inkcre_core.persistence.info_base.storage import StorageRepository
from inkcre_core.utils.base import AIOHTTP_CONNECTOR_GETTER

from .external import ExternalWritableStorage, serialize_uuid_pointer
from .http import (
  DEFAULT_MAX_RESPONSE_BYTES,
  StorageContentTooLargeError,
  read_response_bytes,
)


class RemoteStorageConfig(sqlmodel.SQLModel):
  model_config = pydantic.ConfigDict(extra="forbid", hide_input_in_errors=True)

  endpoint: str = pydantic.Field(
    min_length=1, description="HTTP(S) collection URL including any object prefix."
  )
  timeout: int = pydantic.Field(default=30, gt=0, strict=True)
  max_bytes: int = pydantic.Field(default=DEFAULT_MAX_RESPONSE_BYTES, gt=0, strict=True)

  @pydantic.field_validator("endpoint")
  @classmethod
  def validate_endpoint(cls, value: str) -> str:
    if value != value.strip() or any(char in value for char in "?#\\\r\n\t"):
      raise ValueError(
        "Storage endpoint must be an HTTP(S) URL without a query or fragment"
      )
    url = URL(value)
    if url.scheme not in ("http", "https") or not url.host or "@" in urlsplit(value).netloc:
      raise ValueError("Storage endpoint must be an absolute HTTP(S) URL without userinfo")
    return str(url).rstrip("/")


class StorageRequestError(OSError):
  """A remote storage rejected an operation."""

  def __init__(self, method: str, status: int):
    super().__init__(f"Storage {method} failed with HTTP {status}")


class RemoteHTTPStorage[ConfigT: RemoteStorageConfig](ExternalWritableStorage[ConfigT]):
  _update_conflicts: ClassVar[tuple[int, ...]] = (404, 412)

  def serialize_pointer(self, pointer: str) -> str:
    return serialize_uuid_pointer(pointer)

  async def get_raw_content(self, block_content: str) -> bytes:
    async with self._request("GET", block_content) as response:
      if response.status == 404:
        raise LookupError(f"Storage object {block_content} not found")
      if response.status != 200:
        raise StorageRequestError("GET", response.status)
      return await read_response_bytes(response, self._config.max_bytes)

  async def write_content(self, content: bytes, storage: StorageRepository | None) -> str:
    pointer = str(uuid4())
    async with self._request(
      "PUT", pointer, content=content, headers={"If-None-Match": "*"}
    ) as response:
      self._require_success("PUT", response.status)
    return pointer

  async def update_content(
    self, block_content: str, content: bytes, storage: StorageRepository
  ) -> bool:
    self._check_size(content)
    async with self._request("HEAD", block_content) as response:
      if response.status == 404:
        return False
      self._require_success("HEAD", response.status)
      condition = self._update_condition(response)
    async with self._request(
      "PUT", block_content, content=content, headers={"If-Match": condition}
    ) as response:
      if response.status in self._update_conflicts:
        return False
      self._require_success("PUT", response.status)
      return True

  async def delete_content(
    self, block_content: str, storage: StorageRepository | None
  ) -> bool:
    async with self._request("DELETE", block_content) as response:
      if response.status != 404:
        self._require_success("DELETE", response.status)
      return True

  def _update_condition(self, response: aiohttp.ClientResponse) -> str:
    return "*"

  @abstractmethod
  def _authorize(
    self, method: str, url: URL, headers: dict[str, str], content: bytes | None
  ) -> None: ...

  @asynccontextmanager
  async def _request(
    self,
    method: str,
    pointer: str,
    *,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
  ) -> AsyncGenerator[aiohttp.ClientResponse, None]:
    url = URL(f"{self._config.endpoint}/{self.serialize_pointer(pointer)}", encoded=True)
    headers = {} if headers is None else headers
    if content is not None:
      self._check_size(content)
      headers["Content-Type"] = "application/octet-stream"
    self._authorize(method, url, headers, content)

    async with aiohttp.ClientSession(
      connector=AIOHTTP_CONNECTOR_GETTER(),
      timeout=aiohttp.ClientTimeout(total=self._config.timeout),
    ) as session:
      async with session.request(
        method, url, data=content, headers=headers, allow_redirects=False
      ) as response:
        yield response

  def _check_size(self, content: bytes) -> None:
    if len(content) > self._config.max_bytes:
      raise StorageContentTooLargeError(
        f"Storage content exceeded {self._config.max_bytes} bytes"
      )

  @staticmethod
  def _require_success(method: str, status: int) -> None:
    # WebDAV Multi-Status can contain failures even though its HTTP status is 2xx.
    if not 200 <= status < 300 or status == 207:
      raise StorageRequestError(method, status)
