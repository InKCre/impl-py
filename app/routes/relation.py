"""Relation Module' API Endpoints"""

__all__ = ["ROUTER"]


import fastapi

from inkcre_core.business.info_base.services import RelationService
from inkcre_core.schemas.info_base.relation import RelationCreateForm
from inkcre_core.schemas.info_base.rest import RelationUpdateForm

from .entities import relation_record
from .validation import database_write


ROUTER = fastapi.APIRouter(
  prefix="/relations",
  tags=["relation"],
)


@ROUTER.post("")
async def create_relation(body: RelationCreateForm) -> dict:
  with database_write():
    return relation_record(
      await RelationService.create(
        from_=body.from_,
        to_=body.to_,
        content=body.content,
      )
    )


@ROUTER.get("/by_block/{block_id}")
async def get_relations_by_block(
  block_id: int,
) -> tuple[dict, ...]:
  return tuple(
    relation_record(item) for item in await RelationService.get(block_id=block_id)
  )


@ROUTER.patch("/{relation_id}")
async def update_relation(relation_id: int, body: RelationUpdateForm) -> dict:
  changes = body.model_dump(exclude_unset=True)
  if "from_block_id" in changes:
    changes["from_"] = changes.pop("from_block_id")
  if "to_block_id" in changes:
    changes["to_"] = changes.pop("to_block_id")
  try:
    with database_write():
      updated = await RelationService.update(relation_id, **changes)
  except ValueError as error:
    raise fastapi.HTTPException(404, str(error)) from error
  return {"type": "relation", **relation_record(updated)}


@ROUTER.delete("/{relation_id}", status_code=204)
async def delete_relation(relation_id: int) -> None:
  if not await RelationService.delete(relation_id):
    raise fastapi.HTTPException(404, f"Relation {relation_id} not found")
