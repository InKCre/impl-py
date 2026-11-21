"""Writable blob storage in a Google Drive folder authorized for one OAuth app."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import hashlib
import json
import re
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


_TOKEN_URL = URL("https://oauth2.googleapis.com/token")
_FILES_URL = URL("https://www.googleapis.com/drive/v3/files")
_UPLOAD_URL = URL("https://www.googleapis.com/upload/drive/v3/files")
_BINARY = "application/octet-stream"
_DRIVE_PARAMS = {"supportsAllDrives": "true"}
_CONTROL_RESPONSE_LIMIT = 64 * 1024
_METADATA_FIELDS = "id,parents,mimeType,trashed,appProperties,sha256Checksum"
_FILE_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
_OBJECT_PROPERTY = "inkcre_object_id"
_STORAGE_PROPERTY = "inkcre_storage_id"


class GoogleDriveStorageConfig(sqlmodel.SQLModel):
  model_config = pydantic.ConfigDict(extra="forbid", hide_input_in_errors=True)

  folder_id: str = pydantic.Field(pattern=_FILE_ID_PATTERN)
  client_id: str = pydantic.Field(min_length=1)
  client_secret: pydantic.SecretStr = pydantic.Field(min_length=1)
  refresh_token: pydantic.SecretStr = pydantic.Field(min_length=1)
  timeout: int = pydantic.Field(default=30, gt=0, strict=True)
  max_bytes: int = pydantic.Field(default=DEFAULT_MAX_RESPONSE_BYTES, gt=0, strict=True)

  @pydantic.field_validator("folder_id")
  @classmethod
  def validate_folder_id(cls, value: str) -> str:
    if value == "root":
      raise ValueError("Use the actual Google Drive folder ID instead of the root alias")
    return value


class _Pointer(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  file_id: str = pydantic.Field(pattern=_FILE_ID_PATTERN)
  object_id: str

  @pydantic.field_validator("object_id")
  @classmethod
  def validate_object_id(cls, value: str) -> str:
    return serialize_uuid_pointer(value)


class GoogleDriveRequestError(OSError):
  def __init__(self, method: str, status: int):
    super().__init__(f"Google Drive {method} failed with HTTP {status}")
    self.status = status


def _require_status(response: aiohttp.ClientResponse, *accepted: int) -> None:
  if response.status not in accepted:
    raise GoogleDriveRequestError(response.method, response.status)


async def _json_object(response: aiohttp.ClientResponse, *accepted: int) -> dict:
  _require_status(response, *accepted)
  body = await read_response_bytes(response, _CONTROL_RESPONSE_LIMIT)
  try:
    value = json.loads(body)
  except (UnicodeError, json.JSONDecodeError):
    raise OSError("Google Drive returned invalid JSON") from None
  if not isinstance(value, dict):
    raise OSError("Google Drive returned a non-object JSON response")
  return value


def _upload_location(response: aiohttp.ClientResponse, target: URL) -> URL:
  error = "Google Drive returned an unexpected upload session URL"
  try:
    upload = URL(response.headers.get("Location", ""), encoded=True)
    origin = upload.origin()
  except ValueError:
    raise OSError(error) from None
  upload_paths = {_UPLOAD_URL.raw_path, target.raw_path}
  allowed_paths = upload_paths | {f"/resumable{path}" for path in upload_paths}
  if (
    origin != target.origin()
    or upload.raw_path.rstrip("/") not in allowed_paths
    or upload.raw_user is not None
    or upload.raw_password is not None
    or upload.raw_fragment
    or not upload.query.get("upload_id")
  ):
    raise OSError(error)
  return upload


class GoogleDriveStorage(
  ExternalWritableStorage[GoogleDriveStorageConfig],
  stg_type="google_drive",
  config_cls=GoogleDriveStorageConfig,
):
  """Google Drive folder for deployment-owned binary content."""

  def serialize_pointer(self, pointer: str) -> str:
    return _Pointer.model_validate_json(pointer).model_dump_json()

  async def get_raw_content(self, block_content: str) -> bytes:
    pointer = _Pointer.model_validate_json(block_content)
    async with self._connect() as client:
      metadata = await self._metadata(client, pointer)
      if metadata is None or not self._owns(metadata, pointer):
        raise LookupError("Google Drive storage object not found")
      async with client.get(
        _FILES_URL / pointer.file_id,
        params={**_DRIVE_PARAMS, "alt": "media"},
        allow_redirects=False,
      ) as response:
        if response.status == 404:
          raise LookupError("Google Drive storage object not found")
        _require_status(response, 200)
        return await read_response_bytes(response, self._config.max_bytes)

  async def write_content(self, content: bytes, storage: StorageRepository | None) -> str:
    self._check_size(content)
    async with self._connect() as client:
      async with client.get(
        _FILES_URL / "generateIds", params={"count": "1"}, allow_redirects=False
      ) as response:
        generated = await _json_object(response, 200)
      ids = generated.get("ids")
      if not isinstance(ids, list) or len(ids) != 1:
        raise OSError("Google Drive did not generate exactly one file ID")
      pointer = _Pointer(file_id=ids[0], object_id=str(uuid4()))
      metadata = {
        "id": pointer.file_id,
        "name": f"inkcre-{pointer.object_id}",
        "mimeType": _BINARY,
        "parents": [self._config.folder_id],
        "appProperties": self._object_properties(pointer),
      }
      try:
        await self._upload(client, pointer, content, metadata=metadata)
      except (
        GoogleDriveRequestError,
        aiohttp.ClientConnectionError,
        aiohttp.ClientPayloadError,
        TimeoutError,
      ) as error:
        if isinstance(error, GoogleDriveRequestError) and not (
          error.status in (408, 409) or 500 <= error.status < 600
        ):
          raise
        # Check for a committed upload using the same ID before returning an error.
        current = await self._metadata(client, pointer)
        if (
          current is None
          or not self._owns(current, pointer)
          or current.get("sha256Checksum") != hashlib.sha256(content).hexdigest()
        ):
          raise
      return pointer.model_dump_json()

  async def update_content(
    self, block_content: str, content: bytes, storage: StorageRepository | None
  ) -> bool:
    pointer = _Pointer.model_validate_json(block_content)
    self._check_size(content)
    async with self._connect() as client:
      metadata = await self._metadata(client, pointer)
      if metadata is None or not self._owns(metadata, pointer):
        return False
      try:
        await self._upload(client, pointer, content)
      except GoogleDriveRequestError as error:
        if error.status == 404:
          return False
        raise
    return True

  async def delete_content(
    self, block_content: str, storage: StorageRepository | None
  ) -> bool:
    pointer = _Pointer.model_validate_json(block_content)
    async with self._connect() as client:
      metadata = await self._metadata(client, pointer)
      if metadata is None:
        return True
      if not self._owns(metadata, pointer):
        return False
      async with client.delete(
        _FILES_URL / pointer.file_id, params=_DRIVE_PARAMS, allow_redirects=False
      ) as response:
        _require_status(response, 204, 404)
    return True

  @asynccontextmanager
  async def _connect(self) -> AsyncGenerator[aiohttp.ClientSession, None]:
    config = self._config
    async with aiohttp.ClientSession(
      connector=AIOHTTP_CONNECTOR_GETTER(),
      timeout=aiohttp.ClientTimeout(total=config.timeout),
      cookie_jar=aiohttp.DummyCookieJar(),
    ) as client:
      async with client.post(
        _TOKEN_URL,
        data={
          "grant_type": "refresh_token",
          "client_id": config.client_id,
          "client_secret": config.client_secret.get_secret_value(),
          "refresh_token": config.refresh_token.get_secret_value(),
        },
        allow_redirects=False,
      ) as response:
        result = await _json_object(response, 200)
      token = result.get("access_token")
      if (
        not isinstance(token, str)
        or re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token) is None
        or str(result.get("token_type", "")).lower() != "bearer"
      ):
        raise OSError("Google OAuth did not return a bearer access token")
      client.headers["Authorization"] = f"Bearer {token}"
      yield client

  async def _metadata(
    self, client: aiohttp.ClientSession, pointer: _Pointer
  ) -> dict | None:
    async with client.get(
      _FILES_URL / pointer.file_id,
      params={**_DRIVE_PARAMS, "fields": _METADATA_FIELDS},
      allow_redirects=False,
    ) as response:
      if response.status == 404:
        return None
      return await _json_object(response, 200)

  def _object_properties(self, pointer: _Pointer) -> dict[str, str]:
    return {_OBJECT_PROPERTY: pointer.object_id, _STORAGE_PROPERTY: str(self.storage_id)}

  def _owns(self, metadata: dict, pointer: _Pointer) -> bool:
    properties = metadata.get("appProperties")
    return (
      metadata.get("id") == pointer.file_id
      and metadata.get("mimeType") == _BINARY
      and metadata.get("trashed", False) is False
      and metadata.get("parents") == [self._config.folder_id]
      and isinstance(properties, dict)
      and properties.get(_OBJECT_PROPERTY) == pointer.object_id
      and properties.get(_STORAGE_PROPERTY) == str(self.storage_id)
    )

  async def _upload(
    self,
    client: aiohttp.ClientSession,
    pointer: _Pointer,
    content: bytes,
    *,
    metadata: dict | None = None,
  ) -> None:
    # Drive expects PATCH only when opening an update. Upload sessions always use PUT.
    method, target = (
      ("PATCH", _UPLOAD_URL / pointer.file_id)
      if metadata is None
      else (
        "POST",
        _UPLOAD_URL,
      )
    )
    params = {**_DRIVE_PARAMS, "fields": "id"}
    if not content:
      # An empty PUT to a resumable URI is used to query progress.
      body = aiohttp.MultipartWriter("related")
      body.append_json(
        metadata or {}, headers={"Content-Type": "application/json; charset=UTF-8"}
      )
      body.append(content, headers={"Content-Type": _BINARY})
      async with client.request(
        method,
        target,
        params={**params, "uploadType": "multipart"},
        data=body,
        allow_redirects=False,
      ) as response:
        result = await _json_object(response, 200, 201)
    else:
      async with client.request(
        method,
        target,
        params={**params, "uploadType": "resumable"},
        json=metadata or {},
        headers={
          "Content-Type": "application/json; charset=UTF-8",
          "X-Upload-Content-Type": _BINARY,
          "X-Upload-Content-Length": str(len(content)),
        },
        allow_redirects=False,
      ) as response:
        _require_status(response, 200)
        upload = _upload_location(response, target)
      async with client.put(
        upload,
        data=content,
        headers={"Content-Type": _BINARY},
        allow_redirects=False,
      ) as response:
        result = await _json_object(response, 200, 201)
    if result.get("id") != pointer.file_id:
      raise OSError("Google Drive returned an unexpected file ID after upload")

  def _check_size(self, content: bytes) -> None:
    if len(content) > self._config.max_bytes:
      raise StorageContentTooLargeError(
        f"Storage content exceeded {self._config.max_bytes} bytes"
      )
