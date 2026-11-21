"""Writable byte storage in a WebDAV collection."""

from base64 import b64encode

import pydantic
from yarl import URL

from .remote import RemoteHTTPStorage, RemoteStorageConfig


class WebDAVStorageConfig(RemoteStorageConfig):
  username: str = pydantic.Field(min_length=1, pattern=r"^[^:]+$")
  password: pydantic.SecretStr = pydantic.Field(min_length=1)


class WebDAVStorage(
  RemoteHTTPStorage[WebDAVStorageConfig], stg_type="webdav", config_cls=WebDAVStorageConfig
):
  """WebDAV collection for deployment-owned binary content."""

  def _authorize(
    self, method: str, url: URL, headers: dict[str, str], content: bytes | None
  ) -> None:
    credentials = f"{self._config.username}:{self._config.password.get_secret_value()}"
    headers["Authorization"] = "Basic " + b64encode(credentials.encode()).decode("ascii")
