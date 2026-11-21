"""Writable storage in a dedicated SFTP directory."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging
from pathlib import PurePosixPath
from typing import Self
from uuid import uuid4

import asyncssh
import pydantic
import sqlmodel

from inkcre_core.persistence.info_base.storage import StorageRepository

from .external import ExternalWritableStorage, serialize_uuid_pointer
from .http import DEFAULT_MAX_RESPONSE_BYTES, StorageContentTooLargeError


logger = logging.getLogger(__name__)
_NOT_FOUND = (asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath)


class SFTPStorageConfig(sqlmodel.SQLModel):
  model_config = pydantic.ConfigDict(extra="forbid", hide_input_in_errors=True)

  host: str = pydantic.Field(min_length=1)
  port: int = pydantic.Field(default=22, ge=1, le=65535, strict=True)
  username: str = pydantic.Field(min_length=1)
  known_hosts: str = pydantic.Field(min_length=1)
  directory: str = pydantic.Field(min_length=1)
  password: pydantic.SecretStr | None = pydantic.Field(default=None, min_length=1)
  private_key: pydantic.SecretStr | None = pydantic.Field(default=None, min_length=1)
  private_key_passphrase: pydantic.SecretStr | None = pydantic.Field(
    default=None, min_length=1
  )
  timeout: int = pydantic.Field(default=30, gt=0, strict=True)
  max_bytes: int = pydantic.Field(default=DEFAULT_MAX_RESPONSE_BYTES, gt=0, strict=True)

  @pydantic.field_validator("directory")
  @classmethod
  def validate_directory(cls, value: str) -> str:
    directory = PurePosixPath(value)
    if not directory.is_absolute() or ".." in directory.parts or "\x00" in value:
      raise ValueError("SFTP storage directory must be an absolute path without traversal")
    return str(directory)

  @pydantic.model_validator(mode="after")
  def validate_authentication(self) -> Self:
    if (self.password is None) == (self.private_key is None):
      raise ValueError("Configure either an SFTP password or a private key")
    if self.private_key_passphrase is not None and self.private_key is None:
      raise ValueError("An SFTP passphrase requires a private key")
    if not self.known_hosts.strip():
      raise ValueError("Configure a trusted SSH host key in known_hosts")
    return self


class SFTPStorage(
  ExternalWritableStorage[SFTPStorageConfig], stg_type="sftp", config_cls=SFTPStorageConfig
):
  """SFTP directory for deployment-owned binary content."""

  def __post_init__(self) -> None:
    config = self._config
    self._directory = PurePosixPath(config.directory)
    self._known_hosts = asyncssh.import_known_hosts(config.known_hosts)
    passphrase = config.private_key_passphrase
    self._client_key = (
      asyncssh.import_private_key(
        config.private_key.get_secret_value(),
        passphrase.get_secret_value() if passphrase is not None else None,
      )
      if config.private_key is not None
      else None
    )

  def serialize_pointer(self, pointer: str) -> str:
    return serialize_uuid_pointer(pointer)

  @asynccontextmanager
  async def _connect(self) -> AsyncGenerator[asyncssh.SFTPClient, None]:
    config = self._config
    async with asyncio.timeout(config.timeout):
      async with asyncssh.connect(
        config.host,
        config.port,
        username=config.username,
        known_hosts=self._known_hosts,
        client_keys=[self._client_key] if self._client_key is not None else None,
        password=config.password.get_secret_value()
        if config.password is not None
        else None,
        preferred_auth="publickey" if self._client_key is not None else "password",
        agent_path=None,
        config=None,
      ) as connection:
        # Closing this connection also releases its SFTP session.
        yield await connection.start_sftp_client()

  async def get_raw_content(self, block_content: str) -> bytes:
    path = self._object_directory(block_content) / "data"
    async with self._connect() as sftp:
      try:
        stream = await sftp.open(path, "rb", encoding=None)
        content = await stream.read(self._config.max_bytes + 1)
        await stream.close()
      except _NOT_FOUND as exc:
        raise LookupError(f"Storage object {block_content} not found") from exc
    self._check_size(content)
    return content

  async def write_content(self, content: bytes, storage: StorageRepository | None) -> str:
    self._check_size(content)
    pointer = str(uuid4())
    async with self._connect() as sftp, self._stage(sftp, content) as staged:
      await sftp.rename(staged, self._object_directory(pointer))
    return pointer

  async def update_content(
    self, block_content: str, content: bytes, storage: StorageRepository | None
  ) -> bool:
    self._check_size(content)
    target = self._object_directory(block_content)
    async with self._connect() as sftp, self._stage(sftp, content) as staged:
      # A delete moves the whole object directory. Replacing its child cannot recreate it.
      try:
        await sftp.posix_rename(staged / "data", target / "data")
      except _NOT_FOUND:
        return False
    return True

  async def delete_content(
    self, block_content: str, storage: StorageRepository | None
  ) -> bool:
    target = self._object_directory(block_content)
    discarded = self._directory / f".inkcre-delete-{uuid4()}"
    async with self._connect() as sftp:
      try:
        await sftp.rename(target, discarded)
      except _NOT_FOUND:
        return True
      await self._discard(sftp, discarded)
    return True

  def _object_directory(self, pointer: str) -> PurePosixPath:
    return self._directory / self.serialize_pointer(pointer)

  def _check_size(self, content: bytes) -> None:
    if len(content) > self._config.max_bytes:
      raise StorageContentTooLargeError(
        f"Storage content exceeded {self._config.max_bytes} bytes"
      )

  @asynccontextmanager
  async def _stage(
    self, sftp: asyncssh.SFTPClient, content: bytes
  ) -> AsyncGenerator[PurePosixPath, None]:
    staged = self._directory / f".inkcre-stage-{uuid4()}"
    await sftp.mkdir(staged, asyncssh.SFTPAttrs(permissions=0o700))
    try:
      stream = await sftp.open(staged / "data", "wb", encoding=None)
      await stream.write(content)
      await stream.close()
      yield staged
    finally:
      task = asyncio.current_task()
      # A server stuck on the cancelled request may never answer a cleanup request.
      if task is None or not task.cancelling():
        await self._discard(sftp, staged)

  async def _discard(self, sftp: asyncssh.SFTPClient, directory: PurePosixPath) -> None:
    try:
      try:
        await sftp.remove(directory / "data")
      except _NOT_FOUND:
        pass
      await sftp.rmdir(directory)
    except _NOT_FOUND:
      pass
    except (OSError, asyncssh.Error):
      logger.warning(
        "Could not clean SFTP directory %s in storage %s", directory.name, self.storage_id
      )
