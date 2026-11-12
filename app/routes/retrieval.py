"""Ordinary retrieval facades, distinct from non-delegating Peer inbounds."""

import typing

import fastapi

from inkcre_core.business.lexical_retrieval import LexicalRetrievalManager
from inkcre_core.business.semantic_retrieval import (
  SemanticRetrievalManager,
  SemanticRetrievalNotConfiguredError,
  EmbeddingProfileNotFoundError,
)
from inkcre_core.schemas.ai import EmbeddingProfileModel
from inkcre_core.schemas.peer import PeerRef
from inkcre_core.schemas.lexical_retrieval import (
  LexicalRetrievalRequest,
  LexicalRetrievalResult,
)
from inkcre_core.schemas.semantic_retrieval import SemanticRetrievalRequest

from .entities import relation_record


ROUTER = fastapi.APIRouter(tags=["retrieval"])


@ROUTER.post("/retrieval/lexical")
async def retrieve_lexical(
  body: LexicalRetrievalRequest, route_to_peer: PeerRef | None = None
) -> LexicalRetrievalResult:
  return await LexicalRetrievalManager.retrieve(
    body.query, body.limit, filters=body.filters, route_to_peer=route_to_peer
  )


@ROUTER.post("/retrieval/semantic")
async def retrieve_semantic(
  body: SemanticRetrievalRequest, route_to_peer: PeerRef | None = None
) -> dict[str, typing.Any]:
  try:
    result = await SemanticRetrievalManager.retrieve(
      body.query, body.profile, body.options, route_to_peer=route_to_peer
    )
  except SemanticRetrievalNotConfiguredError as error:
    raise fastapi.HTTPException(409, str(error)) from error
  except EmbeddingProfileNotFoundError as error:
    raise fastapi.HTTPException(404, str(error)) from error
  projected = result.model_dump(mode="json")
  for match, output in zip(result.matches, projected["matches"]):
    if match.type == "relation":
      output["entity"] = relation_record(match.entity)
  return projected


@ROUTER.get("/embedding-profiles")
async def list_embedding_profiles(
  limit: int | None = fastapi.Query(None, gt=0), cursor: int | None = None
) -> dict[str, typing.Any]:
  rows, next_cursor = await SemanticRetrievalManager.list_profiles(
    limit=limit, cursor=cursor
  )
  return {"profiles": rows, "next_cursor": next_cursor}


@ROUTER.get("/embedding-profiles/{profile_id}")
async def get_embedding_profile(profile_id: int) -> EmbeddingProfileModel:
  result = await SemanticRetrievalManager.get_profile(profile_id)
  if result is None:
    raise fastapi.HTTPException(404, f"Embedding profile {profile_id} not found")
  return result
