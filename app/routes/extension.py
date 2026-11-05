"""Core’s v2 Peer-local Extension management API."""

import typing

import fastapi
import pydantic

from app.business.extension import (
  EXTENSION_HOST,
  EXTENSION_MANAGEMENT_CAPABILITY,
  InstalledExtension,
)
from app.business.extension.errors import ExtensionHostError
from inkcre_core.business.peer import PeerHTTPInbound, PeerManager
from inkcre_core.schemas.extension import (
  DisableExtensionCommand,
  EnableExtensionCommand,
  ExtensionManagementCommand,
  ExtensionPeerStatus,
  SelectExtensionCommand,
  SelectExtensionInput,
  UnselectExtensionCommand,
)
from inkcre_core.schemas.peer import PeerRef

from .validation import request_input


ROUTER = fastapi.APIRouter(tags=["extension"])
PEER_INBOUND = PeerHTTPInbound(
  capability=EXTENSION_MANAGEMENT_CAPABILITY,
  method="POST",
  path="/extension-management/v2",
)


def _coordinate(namespace: str, name: str) -> str:
  return f"{namespace}/{name}"


def _raise_http_error(error: ExtensionHostError) -> typing.NoReturn:
  if isinstance(error, pydantic.ValidationError):
    status_code = fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT
  elif "not installed" in str(error) or "no selection" in str(error):
    status_code = fastapi.status.HTTP_404_NOT_FOUND
  else:
    status_code = fastapi.status.HTTP_409_CONFLICT
  raise fastapi.HTTPException(status_code=status_code, detail=str(error)) from error


@ROUTER.get("/extensions")
async def list_extensions(
  limit: int | None = fastapi.Query(None, gt=0), cursor: str | None = None
) -> dict[str, typing.Any]:
  rows = sorted(
    (row for row in await EXTENSION_HOST.list() if cursor is None or row.name > cursor),
    key=lambda row: row.name,
  )
  more = limit is not None and len(rows) > limit
  rows = rows[:limit]
  return {"extensions": rows, "next_cursor": rows[-1].name if more else None}


@ROUTER.get("/extensions/{namespace}/{name}")
async def get_extension(namespace: str, name: str) -> InstalledExtension:
  try:
    return await EXTENSION_HOST.get(_coordinate(namespace, name))
  except ExtensionHostError as error:
    _raise_http_error(error)


@ROUTER.post("/extensions/{namespace}/{name}/selection")
async def select_extension(
  namespace: str,
  name: str,
  body: SelectExtensionInput,
  route_to_peer: PeerRef | None = None,
) -> ExtensionPeerStatus:
  try:
    command = SelectExtensionCommand(
      action="select",
      extension=_coordinate(namespace, name),
      distribution=body.distribution,
      version=body.version,
    )
    return await EXTENSION_HOST.manage(
      command, route_to_peer=route_to_peer or PeerManager.get_current_peer_ref()
    )
  except ExtensionHostError as error:
    _raise_http_error(error)


@ROUTER.delete("/extensions/{namespace}/{name}/selection")
async def unselect_extension(
  namespace: str, name: str, route_to_peer: PeerRef | None = None
) -> ExtensionPeerStatus:
  try:
    command = UnselectExtensionCommand(
      action="unselect", extension=_coordinate(namespace, name)
    )
    return await EXTENSION_HOST.manage(
      command, route_to_peer=route_to_peer or PeerManager.get_current_peer_ref()
    )
  except ExtensionHostError as error:
    _raise_http_error(error)


@ROUTER.put("/extensions/{namespace}/{name}/config")
async def update_extension_config(
  namespace: str,
  name: str,
  body: dict[str, typing.Any] = fastapi.Body(...),
) -> InstalledExtension:
  try:
    with request_input():
      return await EXTENSION_HOST.update_config(
        _coordinate(namespace, name),
        body,
        peer_id=PeerManager.get_current_peer_ref(),
      )
  except ExtensionHostError as error:
    _raise_http_error(error)


@ROUTER.patch("/extensions/{namespace}/{name}/config")
async def patch_extension_config(
  namespace: str, name: str, body: dict[str, typing.Any] = fastapi.Body(...)
) -> InstalledExtension:
  try:
    with request_input():
      return await EXTENSION_HOST.patch_config(
        _coordinate(namespace, name),
        body,
        peer_id=PeerManager.get_current_peer_ref(),
      )
  except ExtensionHostError as error:
    _raise_http_error(error)


@ROUTER.post("/extensions/{namespace}/{name}/enable")
async def enable_extension(
  namespace: str, name: str, route_to_peer: PeerRef | None = None
) -> ExtensionPeerStatus:
  try:
    return await EXTENSION_HOST.manage(
      EnableExtensionCommand(action="enable", extension=_coordinate(namespace, name)),
      route_to_peer=route_to_peer or PeerManager.get_current_peer_ref(),
    )
  except ExtensionHostError as error:
    _raise_http_error(error)


@ROUTER.post("/extensions/{namespace}/{name}/disable")
async def disable_extension(
  namespace: str, name: str, route_to_peer: PeerRef | None = None
) -> ExtensionPeerStatus:
  try:
    return await EXTENSION_HOST.manage(
      DisableExtensionCommand(action="disable", extension=_coordinate(namespace, name)),
      route_to_peer=route_to_peer or PeerManager.get_current_peer_ref(),
    )
  except ExtensionHostError as error:
    _raise_http_error(error)


@ROUTER.post("/extension-management/v2", include_in_schema=False)
async def manage_extension_v2(body: ExtensionManagementCommand) -> ExtensionPeerStatus:
  try:
    return await EXTENSION_HOST.manage_local(body)
  except ExtensionHostError as error:
    _raise_http_error(error)


@ROUTER.post("/extension-management", include_in_schema=False, response_model=None)
async def reject_legacy_extension_management() -> typing.NoReturn:
  raise fastapi.HTTPException(
    status_code=fastapi.status.HTTP_410_GONE,
    detail="Extension management v1 is retired; use core.extension.management.v2",
  )
