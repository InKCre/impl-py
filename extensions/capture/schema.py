"""Capture v1 wire and graph contracts shared with native and web Peers."""

from datetime import UTC
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


MAX_MATERIAL_BYTES = 32 * 1024 * 1024
MAX_REQUEST_BYTES = 48 * 1024 * 1024
CAPTURE_RESOLVER = "extensions.capture.capture.v1"
ATTACHMENT_RESOLVER = "extensions.capture.attachment.v1"
CAPTURE_CAPABILITY = "extensions.capture.submit.v1"
ImageMIME = Literal["image/png", "image/jpeg", "image/gif", "image/webp"]


class ContractModel(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)


class CaptureOrigin(ContractModel):
  application_id: str | None = Field(default=None, max_length=1024)
  title: str | None = Field(default=None, max_length=4096)
  url: str | None = Field(default=None, max_length=8192)


class CaptureMetadata(ContractModel):
  captured_at: AwareDatetime
  entry: Literal["selection", "paste", "screenshot", "manual"]
  origin: CaptureOrigin | None = None

  @field_validator("captured_at")
  @classmethod
  def utc_time(cls, value: AwareDatetime) -> AwareDatetime:
    return value.astimezone(UTC)


class TextMaterial(ContractModel):
  kind: Literal["text"]
  text: str = Field(min_length=1, max_length=MAX_MATERIAL_BYTES)


class ImageMaterial(ContractModel):
  kind: Literal["image"]
  mime_type: ImageMIME
  data_base64: str = Field(min_length=1, max_length=MAX_REQUEST_BYTES)
  filename: str | None = Field(default=None, max_length=512)


Material = Annotated[TextMaterial | ImageMaterial, Field(discriminator="kind")]


class CaptureRequest(CaptureMetadata):
  submission_id: UUID
  materials: tuple[Material, ...] = Field(default=(), max_length=128)
  annotation: str | None = Field(default=None, max_length=MAX_MATERIAL_BYTES)


class AttachmentMetadata(ContractModel):
  mime_type: ImageMIME
  filename: str | None = Field(default=None, max_length=512)


class SolvedMaterial(ContractModel):
  block_id: int
  resolver: str


class SolvedAttachment(ContractModel):
  metadata: AttachmentMetadata
  content_block_id: int


class SolvedCapture(ContractModel):
  metadata: CaptureMetadata
  materials: tuple[SolvedMaterial, ...]
  annotation_block_id: int | None
