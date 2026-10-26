"""Run-local extraction inputs; results are ordinary text Blocks and relations."""

import typing

import pydantic

from app.schemas.info_base.block import BlockID
from app.schemas.info_base.relation import RelationModel


def _non_blank(value: str) -> str:
  if not value.strip():
    raise ValueError("text must not be blank")
  return value


NonBlankText = typing.Annotated[str, pydantic.AfterValidator(_non_blank)]


class ExtractionRequest(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  block_id: BlockID


class ExtractionReadResult(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  observation_id: str
  source_block_id: BlockID
  text: str
  label: NonBlankText
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
