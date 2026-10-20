"""Bounded HTTP/WSGI adapter; credentials and transaction commits precede success."""

import base64
import binascii
from functools import partial
import io
import json
import secrets
import sys

import anyio
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from radicale.app import Application
from radicale import httputils
from radicale.config import load

from app.business.extension import EXTENSION_HOST
from .config import CalDAVConfig
from .storage import DAVPreconditionError, move_etag

MAX_BODY = 16 * 1024 * 1024
METHODS = [
  "OPTIONS",
  "PROPFIND",
  "REPORT",
  "GET",
  "HEAD",
  "PUT",
  "DELETE",
  "MKCOL",
  "MKCALENDAR",
  "PROPPATCH",
  "MOVE",
]


async def authenticate(request: Request) -> None:
  authorization = request.headers.get("authorization", "")
  try:
    scheme, encoded = authorization.split(" ", 1)
    if scheme.lower() != "basic":
      raise ValueError("Invalid scheme")
    username, password = (
      base64.b64decode(encoded, validate=True).decode("utf-8").split(":", 1)
    )
  except (ValueError, UnicodeError, binascii.Error):
    raise unauthorized() from None
  installed = await EXTENSION_HOST.get("inkcre/caldav")
  try:
    configured = CalDAVConfig.model_validate(installed.config).password
  except ValidationError:
    raise unauthorized() from None
  if (
    username != "inkcre"
    or configured is None
    or not secrets.compare_digest(password.encode(), configured.encode())
  ):
    raise unauthorized()


def unauthorized() -> HTTPException:
  return HTTPException(
    401,
    "Invalid DAV credentials",
    headers={"WWW-Authenticate": 'Basic realm="InKCre", charset="UTF-8"'},
  )


class DAVApplication(Application):
  def do_MOVE(self, environ, base_prefix, path, user, request_info):
    token = move_etag.set(environ.get("HTTP_IF_MATCH"))
    try:
      return super().do_MOVE(environ, base_prefix, path, user, request_info)
    except DAVPreconditionError:
      return httputils.PRECONDITION_FAILED
    finally:
      move_etag.reset(token)


def create_application() -> Application:
  configuration = load([])
  configuration.update(
    {
      "auth": {"type": "remote_user"},
      "rights": {"type": "owner_only", "permit_overwrite_collection": "False"},
      "storage": {
        "type": "extensions.caldav.storage",
        "strict_preconditions": "True",
        "predefined_collections": json.dumps(
          {
            "calendar": {
              "tag": "VCALENDAR",
              "D:displayname": "InKCre 日历",
              "C:supported-calendar-component-set": "VEVENT",
            },
            "tasks": {
              "tag": "VCALENDAR",
              "D:displayname": "InKCre 任务",
              "C:supported-calendar-component-set": "VTODO",
            },
            "contacts": {"tag": "VADDRESSBOOK", "D:displayname": "InKCre 联系人"},
          }
        ),
      },
      "web": {"type": "none"},
      "server": {
        "max_content_length": str(MAX_BODY),
        "max_resource_size": str(MAX_BODY // 2),
      },
      "logging": {
        "bad_put_request_content": "False",
        "request_header_on_debug": "False",
        "request_content_on_debug": "False",
        "response_header_on_debug": "False",
        "response_content_on_debug": "False",
      },
    },
    "InKCre CalDAV extension",
  )
  return DAVApplication(configuration)


def invoke(application: Application, environ: dict) -> Response:
  status_code = 500
  response_headers = []

  def start_response(status, headers, exc_info=None):
    nonlocal status_code, response_headers
    if exc_info:
      raise exc_info[1].with_traceback(exc_info[2])
    status_code = int(status.split(" ", 1)[0])
    response_headers = headers

  result = application(environ, start_response)
  try:
    body = b"".join(result)
  finally:
    close = getattr(result, "close", None)
    if close is not None:
      close()
  response = Response(body, status_code=status_code)
  response.raw_headers = [
    (name.lower().encode("latin-1"), value.encode("latin-1"))
    for name, value in response_headers
  ]
  return response


def register_protocol(router: APIRouter) -> None:
  application = create_application()

  async def dispatch(request: Request, dav_path: str = "") -> Response:
    await authenticate(request)
    body = bytearray()
    async for chunk in request.stream():
      if len(body) + len(chunk) > MAX_BODY:
        raise HTTPException(413, "DAV request exceeds 16 MiB")
      body.extend(chunk)
    # WSGI paths use latin-1 encoded UTF-8; never trust a client X-Script-Name.
    script = request.scope.get("root_path", "").rstrip("/") + "/caldav"
    environ = {
      "REQUEST_METHOD": request.method,
      "SCRIPT_NAME": script.encode().decode("latin-1"),
      "PATH_INFO": ("/" + dav_path).encode().decode("latin-1"),
      "QUERY_STRING": request.url.query,
      "SERVER_NAME": request.url.hostname or "localhost",
      "SERVER_PORT": str(
        request.url.port or (443 if request.url.scheme == "https" else 80)
      ),
      "SERVER_PROTOCOL": "HTTP/" + request.scope.get("http_version", "1.1"),
      "REMOTE_USER": "inkcre",
      "CONTENT_TYPE": request.headers.get("content-type", ""),
      "CONTENT_LENGTH": str(len(body)),
      "wsgi.version": (1, 0),
      "wsgi.url_scheme": request.url.scheme,
      "wsgi.input": io.BytesIO(body),
      "wsgi.errors": sys.stderr,
      "wsgi.multithread": True,
      "wsgi.multiprocess": True,
      "wsgi.run_once": False,
    }
    for name in (
      "depth",
      "if-match",
      "if-none-match",
      "destination",
      "overwrite",
      "host",
      "user-agent",
      "accept",
      "accept-encoding",
    ):
      if name in request.headers:
        environ["HTTP_" + name.upper().replace("-", "_")] = request.headers[name]
    return await anyio.to_thread.run_sync(partial(invoke, application, environ))

  router.add_api_route("", dispatch, methods=METHODS, include_in_schema=False)
  router.add_api_route(
    "/{dav_path:path}", dispatch, methods=METHODS, include_in_schema=False
  )
