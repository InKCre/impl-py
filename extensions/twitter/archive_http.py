"""HTTP endpoints implementing the x-monkey remote archive contract."""

from __future__ import annotations

import fastapi
from fastapi import Request, Response

from .archive_schema import ArchiveBatch, ArchiveQuery, QueryResponse, SubmitResponse
from .archive_session import (
  SESSION_COOKIE_NAME,
  SESSION_USE_CLAIM,
  SESSION_USE_TWITTER_ARCHIVE,
  _decode_peer_token,
  renew_session_if_needed,
  validate_archive_request_source,
  x_monkey_base_url,
)
from .reconcile import query_archive, submit_archive

router = fastapi.APIRouter()
CLIENT_HEADER = "x-tweet-db-client"
CLIENT_NAME = "x-monkey"


def _protocol_error(status_code: int, message: str) -> fastapi.responses.JSONResponse:
  return fastapi.responses.JSONResponse(
    {"error": message},
    status_code=status_code,
    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
  )


async def _authorize_archive_request(request: Request, response: Response) -> int | None:
  if not await validate_archive_request_source(request):
    return 403
  content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
  if content_type != "application/json":
    return 415
  if request.headers.get(CLIENT_HEADER) != CLIENT_NAME:
    return 400
  token = request.cookies.get(SESSION_COOKIE_NAME)
  claims = _decode_peer_token(token) if token else None
  if claims is None or claims.get(SESSION_USE_CLAIM) != SESSION_USE_TWITTER_ARCHIVE:
    return 401
  base_url = await x_monkey_base_url()
  if base_url:
    renew_session_if_needed(response, claims, base_url=base_url)
  return None


@router.post(
  "/api/v1/archive/query",
  response_model=QueryResponse,
  response_model_exclude_unset=True,
)
async def archive_query(body: ArchiveQuery, request: Request, response: Response):
  failure = await _authorize_archive_request(request, response)
  if failure is not None:
    return _protocol_error(failure, "archive session or request headers are invalid")
  response.headers["Cache-Control"] = "no-store"
  response.headers["X-Content-Type-Options"] = "nosniff"
  return await query_archive(body)


@router.post(
  "/api/v1/archive/submit",
  response_model=SubmitResponse,
  response_model_exclude_unset=True,
)
async def archive_submit(body: ArchiveBatch, request: Request, response: Response):
  failure = await _authorize_archive_request(request, response)
  if failure is not None:
    return _protocol_error(failure, "archive session or request headers are invalid")
  response.headers["Cache-Control"] = "no-store"
  response.headers["X-Content-Type-Options"] = "nosniff"
  return await submit_archive(body)


__all__ = ["CLIENT_HEADER", "CLIENT_NAME", "router"]
