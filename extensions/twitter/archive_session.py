"""Browser session boundary for the x-monkey archive protocol."""

from __future__ import annotations

from datetime import UTC, datetime
import html
import time
import re
from urllib.parse import parse_qsl, urlsplit

import fastapi
import jwt
from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.business.peer import PeerManager
from app.database_contract.constants import (
  JWT_ALGORITHM,
  JWT_AUDIENCE,
  JWT_ISSUER,
  JWT_ROLE,
)
from app.middleware import decode_peer_jwt
from app.settings import settings

SESSION_USE_CLAIM = "session_use"
SESSION_USE_TWITTER_ARCHIVE = "twitter_archive"
SESSION_COOKIE_NAME = "inkcre_twitter_archive"
SESSION_COOKIE_PATH_SUFFIX = "/twitter/x-monkey/"
SESSION_RENEWAL_REMAINING_FRACTION = 0.5
MAX_CONNECT_FORM_BYTES = 8 * 1024

router = fastapi.APIRouter()


async def x_monkey_base_url() -> str | None:
  """Return the Core's configured public protocol base, preserving its path prefix."""
  try:
    base_url = (await PeerManager.get_current_config_async()).http_public_base_url
  except (RuntimeError, ValueError):
    return None
  return f"{base_url.rstrip('/')}/twitter/x-monkey" if base_url else None


def _session_cookie_path(base_url: str) -> str:
  return f"{urlsplit(base_url).path.rstrip('/')}/"


def _format_expiry(epoch_seconds: int) -> str:
  return datetime.fromtimestamp(epoch_seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_peer_token(token: str) -> dict | None:
  try:
    return decode_peer_jwt(token, settings.jwt_secret)
  except jwt.InvalidTokenError:
    return None


def _decode_session_cookie(request: Request) -> dict | None:
  token = request.cookies.get(SESSION_COOKIE_NAME)
  if not token:
    return None
  claims = _decode_peer_token(token)
  if claims is None or claims.get(SESSION_USE_CLAIM) != SESSION_USE_TWITTER_ARCHIVE:
    return None
  return claims


def _issue_session_token() -> tuple[str, int]:
  issued_at = int(time.time())
  lifetime = settings.twitter_x_monkey_jwt_lifetime_seconds
  expires_at = issued_at + lifetime
  token = jwt.encode(
    {
      "role": JWT_ROLE,
      "iss": JWT_ISSUER,
      "aud": JWT_AUDIENCE,
      "iat": issued_at,
      "exp": expires_at,
      SESSION_USE_CLAIM: SESSION_USE_TWITTER_ARCHIVE,
    },
    settings.jwt_secret,
    algorithm=JWT_ALGORITHM,
  )
  return token, expires_at


def _set_session_cookie(
  response: Response,
  *,
  token: str,
  expires_at: int,
  base_url: str,
) -> None:
  response.set_cookie(
    SESSION_COOKIE_NAME,
    token,
    max_age=max(0, expires_at - int(time.time())),
    path=_session_cookie_path(base_url),
    secure=True,
    httponly=True,
    samesite="none",
  )


def _clear_session_cookie(response: Response, base_url: str | None) -> None:
  path = _session_cookie_path(base_url) if base_url else SESSION_COOKIE_PATH_SUFFIX
  response.delete_cookie(
    SESSION_COOKIE_NAME,
    path=path,
    secure=True,
    httponly=True,
    samesite="none",
  )


def renew_session_if_needed(
  response: Response,
  claims: dict,
  *,
  base_url: str,
) -> int:
  """Renew an eligible session JWT at half of its configured lifetime."""
  expires_at = int(claims["exp"])
  lifetime = settings.twitter_x_monkey_jwt_lifetime_seconds
  if expires_at - int(time.time()) <= lifetime * SESSION_RENEWAL_REMAINING_FRACTION:
    token, expires_at = _issue_session_token()
    _set_session_cookie(response, token=token, expires_at=expires_at, base_url=base_url)
  return expires_at


def _html_page(body: str, *, status_code: int = 200) -> HTMLResponse:
  return HTMLResponse(
    f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>x-monkey 连接</title></head>
<body><main><h1>InKCre x-monkey 连接</h1>{body}</main></body></html>""",
    status_code=status_code,
    headers={
      "Cache-Control": "no-store",
      "Content-Security-Policy": (
        "default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
      ),
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
    },
  )


def _request_origin(request: Request) -> str | None:
  value = request.headers.get("origin")
  if value is None:
    return None
  try:
    parts = urlsplit(value)
    # Accessing .port also rejects malformed authority syntax.
    _ = parts.port
  except ValueError:
    return None
  if (
    parts.scheme not in {"http", "https", "chrome-extension", "moz-extension"}
    or not parts.netloc
    or parts.username is not None
    or parts.password is not None
    or parts.path not in {"", "/"}
    or parts.query
    or parts.fragment
  ):
    return None
  if parts.scheme in {"chrome-extension", "moz-extension"}:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", parts.netloc):
      return None
  elif parts.scheme == "https" and parts.netloc.lower() == "x.com":
    return "https://x.com"
  return f"{parts.scheme}://{parts.netloc}"


async def _core_origin() -> str | None:
  try:
    base_url = (await PeerManager.get_current_config_async()).http_public_base_url
  except (RuntimeError, ValueError):
    return None
  if not base_url:
    return None
  parts = urlsplit(base_url)
  return f"{parts.scheme}://{parts.netloc}"


async def validate_archive_request_source(request: Request) -> bool:
  """Apply the protocol's source policy; Origin is a filter, never a credential."""
  origin = _request_origin(request)
  if origin is None:
    # A missing Origin is expected for some extension-origin GM requests.
    return "origin" not in request.headers
  if origin in {"https://x.com"} or origin.startswith(
    ("chrome-extension://", "moz-extension://")
  ):
    return True
  return origin == await _core_origin()


def _is_top_level_navigation(request: Request) -> bool:
  return (
    request.headers.get("sec-fetch-mode") == "navigate"
    and request.headers.get("sec-fetch-dest") == "document"
  )


async def _connect_form_token(request: Request) -> str | None:
  content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
  if content_type != "application/x-www-form-urlencoded":
    return None
  body = await request.body()
  if not body or len(body) > MAX_CONNECT_FORM_BYTES:
    return None
  try:
    fields = parse_qsl(
      body.decode("utf-8"),
      keep_blank_values=True,
      strict_parsing=True,
      max_num_fields=2,
      encoding="utf-8",
      errors="strict",
    )
  except (UnicodeDecodeError, ValueError):
    return None
  if len(fields) != 1 or fields[0][0] != "token" or not fields[0][1]:
    return None
  return fields[0][1]


@router.post("/session")
async def connect_session(request: Request):
  origin = _request_origin(request)
  if (
    origin is None
    or not origin.startswith(("http://", "https://"))
    or not _is_top_level_navigation(request)
  ):
    return _html_page("<p>请从 InKCre Web 的 Twitter 设置发起连接。</p>", status_code=403)

  token = await _connect_form_token(request)
  claims = _decode_peer_token(token) if token else None
  if claims is None or SESSION_USE_CLAIM in claims:
    return _html_page(
      "<p>连接凭据无效或已失效，请返回 InKCre Web 重新连接。</p>", status_code=401
    )

  base_url = await x_monkey_base_url()
  if base_url is None:
    return _html_page(
      "<p>Core 尚未配置公共 HTTPS 地址，无法建立连接。</p>", status_code=503
    )

  token, expires_at = _issue_session_token()
  response = RedirectResponse(f"{base_url}/account", status_code=303)
  _set_session_cookie(response, token=token, expires_at=expires_at, base_url=base_url)
  return response


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
  claims = _decode_session_cookie(request)
  base_url = await x_monkey_base_url()
  if claims is None:
    return _html_page(
      "<p>尚未连接。请打开已连接此 InKCre 部署的 Web，"
      "在 Twitter 设置中点击“连接 x-monkey”，然后返回脚本检测连接。</p>"
    )

  expires_at = int(claims["exp"])
  if base_url is None:
    return _html_page("<p>连接有效，但 Core 公共 HTTPS 地址未配置。</p>", status_code=503)
  action = html.escape(f"{base_url}/disconnect", quote=True)
  expiry = html.escape(_format_expiry(expires_at))
  return _html_page(
    f"<p>已连接 InKCre Peer：{html.escape(settings.peer_name)}</p>"
    f"<p>会话到期时间：<time>{expiry}</time></p>"
    f'<form method="post" action="{action}">'
    '<button type="submit">断开此浏览器</button></form>'
    "<p>断开只清除此浏览器中的会话，不会删除已保存的信息。</p>"
  )


@router.post("/disconnect")
async def disconnect_session(request: Request):
  if _request_origin(request) != await _core_origin() or not _is_top_level_navigation(
    request
  ):
    return _html_page("<p>断开请求来源无效。</p>", status_code=403)

  base_url = await x_monkey_base_url()
  if base_url is None:
    return _html_page("<p>Core 尚未配置公共 HTTPS 地址。</p>", status_code=503)
  response = RedirectResponse(f"{base_url}/account", status_code=303)
  _clear_session_cookie(response, base_url)
  return response


@router.get("/api/v1/session")
async def get_session(request: Request, response: Response):
  from .archive_schema import SessionResponse

  base_url = await x_monkey_base_url()
  response.headers["Cache-Control"] = "no-store"
  response.headers["X-Content-Type-Options"] = "nosniff"
  account_url = f"{base_url}/account" if base_url else None
  fetch_mode = request.headers.get("sec-fetch-mode")
  if fetch_mode == "no-cors" or not await validate_archive_request_source(request):
    return fastapi.responses.JSONResponse(
      {"error": "request source is not allowed"},
      status_code=403,
      headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )
  claims = _decode_session_cookie(request)
  if claims is None:
    if request.cookies.get(SESSION_COOKIE_NAME):
      _clear_session_cookie(response, base_url)
    return SessionResponse(
      authenticated=False,
      registered=False,
      username=None,
      account_url=account_url,
    )

  if base_url is None:
    return SessionResponse(
      authenticated=False,
      registered=False,
      username=None,
      account_url=None,
    )
  expires_at = renew_session_if_needed(response, claims, base_url=base_url)
  return SessionResponse(
    authenticated=True,
    registered=True,
    username=settings.peer_name,
    expires_at=_format_expiry(expires_at),
    account_url=account_url,
  )


__all__ = [
  "SESSION_COOKIE_NAME",
  "SESSION_RENEWAL_REMAINING_FRACTION",
  "SESSION_USE_CLAIM",
  "SESSION_USE_TWITTER_ARCHIVE",
  "connect_session",
  "disconnect_session",
  "get_session",
  "renew_session_if_needed",
  "router",
  "validate_archive_request_source",
  "x_monkey_base_url",
]
