"""AI model discovery, independent of Agent definition management."""

import typing

import fastapi

from inkcre_core.business.ai import AIManager
from inkcre_core.schemas.ai import AIModelModel


ROUTER = fastapi.APIRouter(prefix="/ai", tags=["ai"])


@ROUTER.get("/models")
async def list_ai_models(
  limit: int | None = fastapi.Query(None, gt=0), cursor: int | None = None
) -> dict[str, typing.Any]:
  rows, next_cursor = await AIManager.list_models(limit=limit, cursor=cursor)
  return {"models": rows, "next_cursor": next_cursor}


@ROUTER.get("/models/{model_id}")
async def get_ai_model(model_id: int) -> AIModelModel:
  model = await AIManager.get_model(model_id)
  if model is None:
    raise fastapi.HTTPException(404, f"AI model {model_id} not found")
  return model
