"""Agent Tool controllers for info-base retrieval and entity reads."""

import typing

import pydantic

from inkcre_core.business.agent import AgentManager
from inkcre_core.business.agent.projection import project_json
from inkcre_core.schemas.ai import JSONValue
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.relation import RelationModel
from inkcre_core.schemas.lexical_retrieval import LexicalRetrievalResult
from inkcre_core.libs.obsrv.telemetry import emit_event

from .main import InfoBaseManager


RETRIEVE_TOOL = "retrieve"
GET_ENTITIES_TOOL = "get_entities"
RetrievalMode: typing.TypeAlias = typing.Literal["lexical", "semantic", "hybrid"]


class RetrieveInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  query: str
  mode: RetrievalMode = "hybrid"
  limit: int = pydantic.Field(default=20, ge=1, le=20)

  @pydantic.field_validator("query")
  @classmethod
  def non_empty_query(cls, value: str) -> str:
    if not value.strip():
      raise ValueError("query must not be empty")
    return value


class EntityReference(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  type: typing.Literal["block", "relation"]
  id: int


class GetEntitiesInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  entities: tuple[EntityReference, ...] = pydantic.Field(default=(), max_length=20)
  random_count: int = pydantic.Field(default=1, ge=1, le=20)

  @pydantic.model_validator(mode="after")
  def validate_selection(self) -> typing.Self:
    if self.entities and self.random_count != 1:
      raise ValueError("random_count only applies when entities is empty")
    return self


@AgentManager.tool(
  RETRIEVE_TOOL,
  description="Retrieve lexical, semantic, or separate hybrid results for one query.",
)
async def retrieve(input: RetrieveInput) -> JSONValue:
  branches = await InfoBaseManager.retrieve(input.query, input.mode, input.limit)
  payload: dict[str, JSONValue] = {}
  for name, branch in branches.items():
    if branch.error is not None:
      payload[name] = {
        "error": type(branch.error).__name__,
        "message": str(branch.error),
      }
      continue
    result = branch.result
    assert result is not None
    if isinstance(result, LexicalRetrievalResult):
      block_ids = tuple(
        match.block.id for match in result.matches if match.block.id is not None
      )
      relation_ids: tuple[int, ...] = ()
    else:
      block_ids = tuple(
        match.entity.id
        for match in result.matches
        if match.type == "block" and match.entity.id is not None
      )
      relation_ids = tuple(
        match.entity.id
        for match in result.matches
        if match.type == "relation" and match.entity.id is not None
      )
    emit_event(
      "inkcre.retrieval.candidates",
      {
        "inkcre.retrieval.method": "lexical" if name == "lexical" else "semantic",
        "inkcre.entity.block_ids": block_ids,
        "inkcre.entity.relation_ids": relation_ids,
        "inkcre.retrieval.candidate_count": len(result.matches),
      },
    )
    if name == "lexical":
      payload[name] = {
        "matches": [
          {
            "entity": {"entity_type": "block", "entity_id": match.block.id},
            **match.model_dump(mode="json", exclude={"block"}),
          }
          for match in typing.cast(typing.Any, result).matches
        ]
      }
    else:
      payload[name] = {
        **result.model_dump(mode="json", exclude={"matches"}),
        "matches": [
          {
            "entity": {"entity_type": match.type, "entity_id": match.entity.id},
            "score": match.score,
          }
          for match in typing.cast(typing.Any, result).matches
        ],
      }
  return payload


@AgentManager.tool(
  GET_ENTITIES_TOOL,
  description=(
    "Read persisted Blocks or Relations without resolving content. "
    "Null may indicate an incorrect entity type."
  ),
)
async def get_entities(input: GetEntitiesInput) -> JSONValue:
  result = await InfoBaseManager.get_entities(
    tuple((reference.type, reference.id) for reference in input.entities),
    random_count=input.random_count,
  )
  emit_event(
    "inkcre.entity.read",
    {
      "inkcre.entity.read.kind": "persisted",
      "inkcre.entity.block_ids": tuple(
        entity.id
        for entity in result
        if isinstance(entity, BlockModel) and entity.id is not None
      ),
      "inkcre.entity.relation_ids": tuple(
        entity.id
        for entity in result
        if isinstance(entity, RelationModel) and entity.id is not None
      ),
      "inkcre.entity.read.count": sum(entity is not None for entity in result),
    },
  )
  return project_json(result)
