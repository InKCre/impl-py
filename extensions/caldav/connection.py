"""Protocol connection details for authenticated deployment Peers."""

import fastapi
from pydantic import BaseModel

from app.business.peer import PeerHTTPInbound
from app.http import get_public_http_base_url
from app.middleware import require_peer_jwt

CONNECTION_INBOUND = PeerHTTPInbound("caldav.connection.v1", "GET", "/caldav/connection")


class DAVConnection(BaseModel):
  server_url: str
  username: str = "inkcre"


def register_connection_route(router: fastapi.APIRouter) -> None:
  @router.get("/connection", dependencies=[fastapi.Depends(require_peer_jwt)])
  async def connection() -> DAVConnection:
    base = await get_public_http_base_url()
    if base is None:
      raise fastapi.HTTPException(409, "Configure this Core's Public HTTP Base URL.")
    return DAVConnection(server_url=f"{base}/caldav/")
