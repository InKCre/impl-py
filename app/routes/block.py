"""Block Module's API Enpoints"""

__all__ = ["ROUTER"]

import fastapi
from inkcre_core.business.info_base.services import BlockService
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.info_base.block import BlockForm
from inkcre_core.schemas.info_base.rest import BlockUpdateForm
from .validation import database_write

ROUTER = fastapi.APIRouter(
  prefix="/blocks",
  tags=["block"],
)


ROUTER.get("/recent")(BlockService.get_recent)


@ROUTER.get("/{block_id}")
async def get_block_by_id(block_id: int) -> BlockModel:
  block = await BlockService.get(block_id)
  if block is None:
    raise fastapi.HTTPException(
      status_code=fastapi.status.HTTP_404_NOT_FOUND,
      detail=f"Block with id {block_id} not found.",
    )
  return block


@ROUTER.post("")
async def create_block(
  body: BlockForm,
  response: fastapi.Response,
) -> BlockModel:
  """创建块"""
  with database_write():
    body = await BlockService.create(body)

  response.status_code = 201
  return body


@ROUTER.patch("/{block_id}")
async def edit_block(
  block_id: int,
  body: BlockUpdateForm,
) -> dict:
  """编辑块（部分更新），只更新请求中提供的字段。"""
  try:
    with database_write():
      updated = await BlockService.edit_block(
        block_id, **body.model_dump(exclude_unset=True)
      )
  except ValueError:
    raise fastapi.HTTPException(
      status_code=fastapi.status.HTTP_404_NOT_FOUND,
      detail=f"Block with id {block_id} not found.",
    )
  else:
    return {"type": "block", **updated.model_dump(mode="json")}


@ROUTER.delete("/{block_id}", status_code=204)
async def delete_block(block_id: int) -> None:
  if not await BlockService.delete(block_id):
    raise fastapi.HTTPException(404, f"Block {block_id} not found")
