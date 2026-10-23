"""Local-files v1 graph metadata; original bytes remain on the owning Peer."""

from typing import Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

FILE_RESOLVER = "extensions.local_files.file.v1"


def file_location(value: str) -> tuple[UUID, str]:
  location = urlsplit(value)
  if (
    location.scheme != "inkcre-peer-file"
    or location.username is not None
    or location.password is not None
    or location.port is not None
    or "?" in value
    or "#" in value
  ):
    raise ValueError("Invalid Peer file location")
  peer = UUID(location.hostname or "")
  path = unquote(location.path, errors="strict")
  if not path.startswith("/") or "\0" in path:
    raise ValueError("Peer file location requires an absolute path")
  return peer, path


class MetadataModel(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)


class FileIdentity(MetadataModel):
  volume: str
  number: int = Field(ge=0)
  created: AwareDatetime


class FileRevision(MetadataModel):
  sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  extractor: str
  extracted_at: AwareDatetime
  scope: Literal["text", "text_layer", "main_document"]


class FileMetadata(MetadataModel):
  location: str
  format: Literal["pdf", "docx", "markdown", "org", "html", "text"]
  identity: FileIdentity | None = None
  available: bool
  observed_at: AwareDatetime
  revision: FileRevision | None = None
  issue: str | None = None

  @field_validator("location")
  @classmethod
  def valid_location(cls, value: str) -> str:
    file_location(value)
    return value


class SolvedFile(MetadataModel):
  metadata: FileMetadata
  body_id: int | None
  text: str | None
