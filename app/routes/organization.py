"""Fixed local organization rumination HTTP inbound."""

__all__ = ["ROUTER"]

import fastapi

from inkcre_core.business.organization import (
  RUMINATION_CAPABILITY,
  RuminationBehaviorResolver,
)
from inkcre_core.business.peer import PeerHTTPInbound
from inkcre_core.schemas.organization import RuminationRequest


ROUTER = fastapi.APIRouter(tags=["organization"])
PEER_INBOUND = PeerHTTPInbound(
  capability=RUMINATION_CAPABILITY,
  method="POST",
  path="/organization/ruminate",
)


@ROUTER.post(
  "/organization/ruminate",
  status_code=fastapi.status.HTTP_204_NO_CONTENT,
)
async def ruminate(body: RuminationRequest) -> None:
  await RuminationBehaviorResolver.ruminate_local(body.block)
