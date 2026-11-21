"""Writable byte storage at a SigV4 S3 bucket endpoint."""

from typing import ClassVar

import aiohttp
import pydantic
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from yarl import URL

from .remote import RemoteHTTPStorage, RemoteStorageConfig


class S3StorageConfig(RemoteStorageConfig):
  region: str = pydantic.Field(min_length=1)
  access_key_id: str = pydantic.Field(min_length=1)
  secret_access_key: pydantic.SecretStr = pydantic.Field(min_length=1)
  session_token: pydantic.SecretStr | None = pydantic.Field(default=None, min_length=1)

  @pydantic.field_validator("endpoint")
  @classmethod
  def encode_s3_endpoint_path(cls, value: str) -> str:
    url = URL(value, encoded=True)
    return str(url.with_path(url.raw_path.replace("+", "%2B"), encoded=True)).rstrip("/")


class S3Storage(
  RemoteHTTPStorage[S3StorageConfig], stg_type="s3", config_cls=S3StorageConfig
):
  """S3-compatible object storage for deployment-owned binary content."""

  _update_conflicts: ClassVar[tuple[int, ...]] = (404, 409, 412)

  def __post_init__(self) -> None:
    config = self._config
    token = config.session_token
    credentials = Credentials(
      config.access_key_id,
      config.secret_access_key.get_secret_value(),
      token.get_secret_value() if token is not None else None,
    )
    self._signer = S3SigV4Auth(credentials, "s3", config.region)

  def _update_condition(self, response: aiohttp.ClientResponse) -> str:
    etag = response.headers.get("ETag")
    if not etag:
      raise OSError("S3 storage HEAD response did not include an ETag")
    return etag

  def _authorize(
    self, method: str, url: URL, headers: dict[str, str], content: bytes | None
  ) -> None:
    request = AWSRequest(method=method, url=str(url), headers=headers, data=content)
    self._signer.add_auth(request)
    headers.update(request.headers.items())
