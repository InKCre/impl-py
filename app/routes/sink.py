"""Peer-authenticated Sink management API."""

import typing

import fastapi
import pydantic

from inkcre_core.business.peer import PeerManager
from app.business.sink import (
  SinkError,
  SinkManager,
  SinkNotFoundError,
  SinkStateConflictError,
  UnknownSinkTypeError,
)
from inkcre_core.schemas.sink import SinkCreateForm, SinkID, SinkModel, SinkTypeModel


ROUTER = fastapi.APIRouter(tags=["sink"])


def _raise_sink_error(error: SinkError) -> typing.NoReturn:
  if isinstance(error, SinkNotFoundError):
    status = fastapi.status.HTTP_404_NOT_FOUND
  elif isinstance(error, UnknownSinkTypeError):
    status = fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT
  elif isinstance(error, SinkStateConflictError):
    status = fastapi.status.HTTP_409_CONFLICT
  else:
    status = fastapi.status.HTTP_500_INTERNAL_SERVER_ERROR
  raise fastapi.HTTPException(status_code=status, detail=str(error)) from error


@ROUTER.get("/sink-types")
async def list_sink_types() -> tuple[SinkTypeModel, ...]:
  return await SinkManager.list_types()


@ROUTER.get("/sink-types/{type_}")
async def get_sink_type(type_: str) -> SinkTypeModel:
  result = await SinkManager.get_type(type_)
  if result is None:
    raise fastapi.HTTPException(404, f"Sink type {type_!r} not found")
  return result


@ROUTER.get("/sinks")
async def list_sinks() -> tuple[SinkModel, ...]:
  return await SinkManager.list()


@ROUTER.post("/sinks", status_code=201)
async def create_sink(body: SinkCreateForm) -> SinkModel:
  try:
    return await SinkManager.create(body.type, nickname=body.nickname, config=body.config)
  except pydantic.ValidationError as error:
    raise fastapi.HTTPException(
      status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
      detail=error.errors(include_url=False, include_context=False),
    ) from error
  except SinkError as error:
    _raise_sink_error(error)


@ROUTER.get("/sinks/{sink_id}")
async def get_sink(sink_id: SinkID) -> SinkModel:
  try:
    return await SinkManager.get(sink_id)
  except SinkError as error:
    _raise_sink_error(error)


@ROUTER.put("/sinks/{sink_id}/config")
async def update_sink_config(
  sink_id: SinkID,
  body: dict[str, typing.Any] = fastapi.Body(...),
) -> SinkModel:
  try:
    return await SinkManager.update_config(sink_id, body)
  except pydantic.ValidationError as error:
    raise fastapi.HTTPException(
      status_code=fastapi.status.HTTP_422_UNPROCESSABLE_CONTENT,
      detail=error.errors(include_url=False, include_context=False),
    ) from error
  except SinkError as error:
    _raise_sink_error(error)


@ROUTER.post("/sinks/{sink_id}/enable")
async def enable_sink(sink_id: SinkID) -> SinkModel:
  try:
    return await SinkManager.enable(sink_id, PeerManager.get_current_peer_ref())
  except SinkError as error:
    _raise_sink_error(error)


@ROUTER.post("/sinks/{sink_id}/disable")
async def disable_sink(sink_id: SinkID) -> SinkModel:
  try:
    return await SinkManager.disable(sink_id, PeerManager.get_current_peer_ref())
  except SinkError as error:
    _raise_sink_error(error)


@ROUTER.delete("/sinks/{sink_id}", status_code=204)
async def delete_sink(sink_id: SinkID) -> fastapi.Response:
  try:
    await SinkManager.delete(sink_id)
  except SinkError as error:
    _raise_sink_error(error)
  return fastapi.Response(status_code=fastapi.status.HTTP_204_NO_CONTENT)
