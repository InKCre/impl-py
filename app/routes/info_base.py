"""Info-base graph command HTTP resource."""

__all__ = ["ROUTER"]

import fastapi

from inkcre_core.business.info_base.commands import submit_graph as submit_graph_command
from inkcre_core.schemas.info_base.main import GraphForm, SubmitGraphResult
from .validation import database_write


ROUTER = fastapi.APIRouter(tags=["info-base"])


@ROUTER.post("/graph")
async def submit_graph(body: GraphForm) -> SubmitGraphResult:
  with database_write():
    return await submit_graph_command(body)
