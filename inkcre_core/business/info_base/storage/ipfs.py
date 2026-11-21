"""Immutable byte storage backed by deployment-owned Kubo pins."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import json
from typing import Literal
from uuid import uuid4

import aiohttp
import pydantic

from inkcre_core.persistence.info_base.storage import StorageRepository
from inkcre_core.utils.base import AIOHTTP_CONNECTOR_GETTER

from .external import ExternalWritableStorage, serialize_uuid_pointer
from .http import StorageContentTooLargeError, read_response_bytes
from .remote import RemoteStorageConfig, StorageRequestError


_RPC_METADATA_LIMIT = 4096


class IPFSStorageConfig(RemoteStorageConfig):
  endpoint: str = pydantic.Field(
    min_length=1, description="Private Kubo HTTP RPC base URL before /api/v0."
  )
  bearer_token: pydantic.SecretStr | None = pydantic.Field(default=None, min_length=1)


class _ObjectPointer(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", hide_input_in_errors=True)

  # The directory root is CIDv1 with dag-pb and a 32-byte SHA-256 digest.
  root_cid: str = pydantic.Field(pattern=r"^bafybei[a-h][a-z2-7]{50}[aeimquy4]$")
  object_id: str

  @pydantic.field_validator("object_id")
  @classmethod
  def validate_object_id(cls, value: str) -> str:
    return serialize_uuid_pointer(value)

  @property
  def root_path(self) -> str:
    return f"/ipfs/{self.root_cid}"

  @property
  def pin_name(self) -> str:
    return f"inkcre-{self.object_id}"


class _AddEntry(pydantic.BaseModel):
  name: str = pydantic.Field(alias="Name")
  cid: str = pydantic.Field(alias="Hash")


class _PinStatus(pydantic.BaseModel):
  name: str = pydantic.Field(alias="Name")
  mode: Literal["recursive"] = pydantic.Field(alias="Type")


class _PinList(pydantic.BaseModel):
  pins: dict[str, _PinStatus] = pydantic.Field(alias="Keys")


class IPFSStorage(
  ExternalWritableStorage[IPFSStorageConfig], stg_type="ipfs", config_cls=IPFSStorageConfig
):
  """Keep one recursively pinned directory per immutable storage object."""

  def serialize_pointer(self, pointer: str) -> str:
    return _ObjectPointer.model_validate_json(pointer).model_dump_json()

  async def get_raw_content(self, block_content: str) -> bytes:
    pointer = _ObjectPointer.model_validate_json(block_content)
    if await self._read_pin_name(pointer) != pointer.pin_name:
      raise LookupError("IPFS storage object not retained by its pin")
    async with self._rpc(
      "cat", {"arg": f"{pointer.root_path}/{pointer.object_id}"}
    ) as response:
      if response.status != 200:
        raise StorageRequestError("IPFS cat", response.status)
      declared_length = response.headers.get("X-Content-Length")
      if declared_length is None or not declared_length.isdecimal():
        raise OSError("Kubo cat did not declare the byte length")
      expected_length = int(declared_length)
      if expected_length > self._config.max_bytes:
        raise StorageContentTooLargeError(
          f"Storage content exceeded {self._config.max_bytes} bytes"
        )
      content = await read_response_bytes(response, self._config.max_bytes)
      # Kubo can report stream failures in HTTP trailers after sending a 200 response.
      if len(content) != expected_length:
        raise OSError("Kubo cat returned an incomplete byte stream")
      return content

  async def write_content(self, content: bytes, storage: StorageRepository | None) -> str:
    if len(content) > self._config.max_bytes:
      raise StorageContentTooLargeError(
        f"Storage content exceeded {self._config.max_bytes} bytes"
      )
    object_id = str(uuid4())
    data = aiohttp.FormData()
    data.add_field(
      "file", content, filename=object_id, content_type="application/octet-stream"
    )
    options = {
      "wrap-with-directory": "true",
      "cid-version": "1",
      "hash": "sha2-256",
      "progress": "false",
      "pin": "true",
      "pin-name": f"inkcre-{object_id}",
    }
    async with self._rpc("add", options, data=data) as response:
      if response.status != 200:
        raise StorageRequestError("IPFS add", response.status)
      body = await read_response_bytes(response, _RPC_METADATA_LIMIT)
    try:
      file, root = [_AddEntry.model_validate_json(line) for line in body.splitlines()]
      pointer = _ObjectPointer(root_cid=root.cid, object_id=object_id)
    except ValueError:
      raise OSError("Kubo add did not return a file and wrapped directory") from None
    if file.name != object_id or root.name != "":
      raise OSError("Kubo add returned unexpected directory entries")
    if await self._read_pin_name(pointer) != pointer.pin_name:
      raise OSError("Kubo did not retain the new object's recursive pin")
    return pointer.model_dump_json()

  async def update_content(
    self, block_content: str, content: bytes, storage: StorageRepository | None
  ) -> bool:
    raise NotImplementedError(
      "IPFS objects are immutable; create a new storage object instead"
    )

  async def delete_content(
    self, block_content: str, storage: StorageRepository | None
  ) -> bool:
    pointer = _ObjectPointer.model_validate_json(block_content)
    pin_name = await self._read_pin_name(pointer)
    if pin_name is None:
      return True
    if pin_name != pointer.pin_name:
      raise ValueError("IPFS pointer does not own this pin")
    options = {"arg": pointer.root_path, "recursive": "true", "offline": "true"}
    async with self._rpc("pin/rm", options) as response:
      if response.status == 200:
        return True
      status = response.status
    # A parallel delete can release the same pin between the two RPC calls.
    if status == 500 and await self._read_pin_name(pointer) is None:
      return True
    raise StorageRequestError("IPFS pin/rm", status)

  async def _read_pin_name(self, pointer: _ObjectPointer) -> str | None:
    options = {
      "arg": pointer.root_path,
      "type": "recursive",
      "names": "true",
      "offline": "true",
    }
    async with self._rpc("pin/ls", options) as response:
      if response.status not in (200, 500):
        raise StorageRequestError("IPFS pin/ls", response.status)
      body = await read_response_bytes(response, _RPC_METADATA_LIMIT)
      if response.status == 500:
        try:
          error = json.loads(body)
        except ValueError:
          raise StorageRequestError("IPFS pin/ls", response.status) from None
        if isinstance(error, dict) and error.get("Message") == (
          f"path '{pointer.root_path}' is not pinned"
        ):
          return None
        raise StorageRequestError("IPFS pin/ls", response.status)
    try:
      pins = _PinList.model_validate_json(body).pins
      return pins[pointer.root_cid].name
    except (ValueError, KeyError):
      raise OSError("Kubo did not return the requested recursive pin") from None

  @asynccontextmanager
  async def _rpc(
    self, command: str, options: dict[str, str], *, data: aiohttp.FormData | None = None
  ) -> AsyncGenerator[aiohttp.ClientResponse, None]:
    token = self._config.bearer_token
    headers = {"Authorization": f"Bearer {token.get_secret_value()}"} if token else {}
    async with aiohttp.ClientSession(
      connector=AIOHTTP_CONNECTOR_GETTER(),
      timeout=aiohttp.ClientTimeout(total=self._config.timeout),
    ) as session:
      async with session.post(
        f"{self._config.endpoint}/api/v0/{command}",
        params={"cid-base": "base32", "enc": "json", **options},
        data=data,
        headers=headers,
        allow_redirects=False,
      ) as response:
        yield response
