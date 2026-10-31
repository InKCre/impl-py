"""Organization rumination configuration, inbound, and Agent Tool forms."""

from app.contracts.jobs import (
  MediaInterpretationJobParameters as MediaInterpretationJobParameters,
  RuminationRequest as RuminationRequest,
)

import typing

import pydantic

from app.schemas.ai import JSONValue
from app.schemas.info_base.main import GraphForm, NegativeBlockID


class RuminationConfig(pydantic.BaseModel):
  """Deployment selection of one reusable Agent definition."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  agent: int


class MediaInterpretationConfig(pydantic.BaseModel):
  """Deployment selection of independent modality-oriented Agents."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  image_agent: int
  audio_agent: int
  video_agent: int


class MediaInterpretationDiagnostic(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  block: int
  modality: typing.Literal["image", "audio", "video"]
  outcome: typing.Literal["unavailable", "failed", "no_output"]
  reason: str


class MediaInterpretationReport(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  selected: int = 0
  interpreted: int = 0
  unavailable: int = 0
  failed: int = 0
  no_output: int = 0
  diagnostics: tuple[MediaInterpretationDiagnostic, ...] = ()


class GetDraftGraphSchemaInput(pydantic.BaseModel):
  """Generic schema-discovery input narrowed when an Agent run binds Tools."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  resolver_types: tuple[str, ...]


class DraftGraphInput(pydantic.BaseModel):
  """Generic draft request; Resolver input is validated by its bound run model."""

  model_config = pydantic.ConfigDict(extra="forbid")

  resolver_type: str
  input: dict[str, JSONValue] = pydantic.Field(
    description="Arguments matching the selected Resolver's input_schema."
  )
  local_block_id_start: NegativeBlockID = pydantic.Field(
    default=-1, description="First temporary ID; keep IDs disjoint when combining drafts."
  )


class SubmitGraphInput(pydantic.BaseModel):
  """The sole rumination Tool input that can mutate the info-base graph."""

  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  graph: GraphForm
