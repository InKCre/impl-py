"""Retained extraction content and inputs to its owning behavior."""

import json
import typing

import pydantic

from app.schemas.info_base.block import BlockID
from app.schemas.info_base.relation import RelationModel


EXTRACTION_RESULT_RESOLVER = "core.organization.extraction-result.v1"
EXTRACTION_SOURCE_RELATION = "core.organization.extraction.source.v1"


def _non_blank(value: str) -> str:
  if not value.strip():
    raise ValueError("text must not be blank")
  return value


NonBlankText = typing.Annotated[str, pydantic.AfterValidator(_non_blank)]


class ExtractionOrigin(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  label: NonBlankText


class ExtractionBasis(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  key: NonBlankText
  observed_origin: ExtractionOrigin
  excerpts: tuple[NonBlankText, ...] = pydantic.Field(min_length=1)


class ExtractionContent(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  text: NonBlankText
  basis: tuple[ExtractionBasis, ...] = pydantic.Field(min_length=1)

  @pydantic.field_validator("basis")
  @classmethod
  def unique_keys(cls, value: tuple[ExtractionBasis, ...]) -> tuple[ExtractionBasis, ...]:
    if len({group.key for group in value}) != len(value):
      raise ValueError("basis keys must be unique within the result")
    return value


class ExtractionSourceRelation(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  kind: typing.Literal["core.organization.extraction.source.v1"] = (
    EXTRACTION_SOURCE_RELATION
  )
  basis_key: NonBlankText

  @classmethod
  def from_content(cls, content: str) -> typing.Self | None:
    try:
      value = json.loads(content)
    except json.JSONDecodeError:
      return None
    if not isinstance(value, dict) or value.get("kind") != EXTRACTION_SOURCE_RELATION:
      return None
    return cls.model_validate(value)


class ExtractionRequest(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  block_id: BlockID


class ExtractionReadResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  observation_id: str
  source_block_id: BlockID
  text: str
  observed_origin: ExtractionOrigin
  relations: tuple[RelationModel, ...]


class ExtractionSelection(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  observation_id: NonBlankText
  excerpts: tuple[NonBlankText, ...] = pydantic.Field(min_length=1)


class ExtractionProposal(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  text: typing.Annotated[
    str,
    pydantic.StringConstraints(strip_whitespace=True),
    pydantic.AfterValidator(_non_blank),
  ]
  sources: tuple[ExtractionSelection, ...] = pydantic.Field(min_length=1)


class ExtractionWriteResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  extraction_block_id: BlockID
  created: bool
