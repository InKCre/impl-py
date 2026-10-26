"""Capture v1 persisted metadata interpreted by the optional Python reader."""

from datetime import UTC
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


CAPTURE_RESOLVER = "extensions.capture.capture.v1"
ATTACHMENT_RESOLVER = "extensions.capture.attachment.v1"
ImageMIME = Literal["image/png", "image/jpeg", "image/gif", "image/webp"]


class ContractModel(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)


class CaptureOrigin(ContractModel):
  application_id: str | None = Field(default=None, max_length=1024)
  title: str | None = Field(default=None, max_length=4096)
  url: str | None = Field(default=None, max_length=8192)


class CaptureMetadata(ContractModel):
  submission_id: UUID
  payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
  captured_at: AwareDatetime
  entry: Literal["selection", "paste", "screenshot", "manual"]
  origin: CaptureOrigin | None = None

  @field_validator("captured_at")
  @classmethod
  def utc_time(cls, value: AwareDatetime) -> AwareDatetime:
    return value.astimezone(UTC)


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
