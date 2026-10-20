"""Browser session boundary for the x-monkey archive protocol."""

from __future__ import annotations

from datetime import UTC, datetime
import html
import secrets
import time
import re
from urllib.parse import urlsplit

import fastapi
import jwt
from fastapi import Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.business.peer import PeerManager
from app.database_contract.constants import (
  JWT_ALGORITHM,
  JWT_AUDIENCE,
  JWT_ISSUER,
  JWT_ROLE,
)
from app.middleware import decode_peer_jwt, require_peer_jwt
from app.settings import settings
from .archive_schema import SessionResponse

SESSION_USE_CLAIM = "session_use"
SESSION_USE_TWITTER_ARCHIVE = "twitter_archive"
SESSION_COOKIE_NAME = "inkcre_twitter_archive"
SESSION_COOKIE_PATH_SUFFIX = "/twitter/x-monkey/"
SESSION_RENEWAL_REMAINING_FRACTION = 0.5
CONNECT_PEER_TOKEN_LIFETIME_SECONDS = 60

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


def _html_page(
  body: str, *, status_code: int = 200, script_nonce: str | None = None
) -> HTMLResponse:
  script_policy = f"script-src 'nonce-{script_nonce}'; " if script_nonce else ""
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
        f"default-src 'none'; {script_policy}connect-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
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


@router.post("/session", response_model=SessionResponse)
async def connect_session(
  request: Request,
  response: Response,
  claims: dict = Depends(require_peer_jwt),
):
  if _request_origin(request) != await _core_origin():
    return JSONResponse({"error": "same-origin request required"}, status_code=403)
  if SESSION_USE_CLAIM in claims:
    return JSONResponse({"error": "ordinary Peer credential required"}, status_code=401)
  content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
  if content_type != "application/json":
    return JSONResponse({"error": "application/json required"}, status_code=415)

  base_url = await x_monkey_base_url()
  if base_url is None:
    return JSONResponse({"error": "public Core URL is not configured"}, status_code=503)

  token, expires_at = _issue_session_token()
  _set_session_cookie(response, token=token, expires_at=expires_at, base_url=base_url)
  response.headers["Cache-Control"] = "no-store"
  response.headers["X-Content-Type-Options"] = "nosniff"
  return SessionResponse(
    authenticated=True,
    registered=True,
    username=settings.peer_name,
    expires_at=_format_expiry(expires_at),
    account_url=f"{base_url}/account",
  )


@router.get("/account", response_class=HTMLResponse)
async def account_page(request: Request):
  claims = _decode_session_cookie(request)
  base_url = await x_monkey_base_url()
  nonce = secrets.token_urlsafe(18)
  connected_state = (
    '<p id="connection-status" role="status">Connected</p>'
    f'<p id="connection-peer">InKCre Peer: {html.escape(settings.peer_name)}</p>'
    if claims is not None
    else '<p id="connection-status" role="status">尚未连接。</p>'
    '<p id="connection-peer" hidden></p>'
  )
  expiry = (
    f'<p id="connection-expiry">会话到期时间：<time id="session-expires-at">'
    f"{html.escape(_format_expiry(int(claims['exp'])))}</time></p>"
    if claims is not None
    else (
      '<p id="connection-expiry" hidden>会话到期时间：'
      '<time id="session-expires-at"></time></p>'
    )
  )
  disconnect = (
    f'<form method="post" action="{html.escape(base_url + "/disconnect", quote=True)}">'
    '<button type="submit">断开此浏览器</button></form>'
    if base_url
    else ""
  )
  script = r"""
<script nonce="__NONCE__">
(() => {
  const input = document.getElementById('jwt-secret');
  const button = document.getElementById('connect');
  const status = document.getElementById('connection-status');
  const peer = document.getElementById('connection-peer');
  const expiry = document.getElementById('connection-expiry');
  const expiresAt = document.getElementById('session-expires-at');
  const encoder = new TextEncoder();
  const base64url = (bytes) => {
    let binary = '';
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/u, '');
  };
  const encode = (value) => base64url(encoder.encode(JSON.stringify(value)));
  const sign = async (secret) => {
    const now = Math.floor(Date.now() / 1000);
    const unsigned = `${encode({alg: 'HS256', typ: 'JWT'})}.${encode({
      role: '__JWT_ROLE__', iss: '__JWT_ISSUER__', aud: '__JWT_AUDIENCE__',
      iat: now, exp: now + __JWT_LIFETIME__
    })}`;
    const key = await crypto.subtle.importKey(
      'raw', encoder.encode(secret), {name: 'HMAC', hash: 'SHA-256'}, false, ['sign']
    );
    const signature = await crypto.subtle.sign('HMAC', key, encoder.encode(unsigned));
    return `${unsigned}.${base64url(new Uint8Array(signature))}`;
  };
  button.addEventListener('click', async () => {
    const secret = input.value;
    input.value = '';
    if (!secret) {
      status.textContent = '请输入 JWT_SECRET。';
      return;
    }
    if (!window.isSecureContext || !globalThis.crypto?.subtle) {
      status.textContent = '连接需要 HTTPS 安全页面与 WebCrypto。';
      return;
    }
    button.disabled = true;
    status.textContent = '正在连接…';
    let credential = '';
    try {
      credential = await sign(secret);
      const response = await fetch('./session', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Authorization': `Bearer ${credential}`,
          'Content-Type': 'application/json'
        },
        body: '{}'
      });
      if (!response.ok) throw new Error('connect');
      const sessionResponse = await fetch('./api/v1/session', {
        credentials: 'same-origin',
        headers: {'Accept': 'application/json'}
      });
      const session = await sessionResponse.json();
      if (!sessionResponse.ok || session.authenticated !== true) throw new Error('session');
      status.textContent = 'Connected';
      peer.textContent = session.username ? `InKCre Peer: ${session.username}` : '';
      peer.hidden = !session.username;
      expiresAt.textContent = session.expires_at || '';
      expiry.hidden = !session.expires_at;
    } catch {
      status.textContent = '连接失败。请确认 JWT_SECRET 与当前 InKCre 部署匹配后重试。';
    } finally {
      credential = '';
      input.value = '';
      button.disabled = false;
    }
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      button.click();
    }
  });
})();
</script>"""
  for placeholder, value in (
    ("__NONCE__", nonce),
    ("__JWT_ROLE__", JWT_ROLE),
    ("__JWT_ISSUER__", JWT_ISSUER),
    ("__JWT_AUDIENCE__", JWT_AUDIENCE),
    ("__JWT_LIFETIME__", str(CONNECT_PEER_TOKEN_LIFETIME_SECONDS)),
  ):
    script = script.replace(placeholder, value)
  return _html_page(
    connected_state
    + expiry
    + "<h2>连接 x-monkey</h2>"
    + '<label for="jwt-secret">JWT_SECRET</label>'
    + '<input id="jwt-secret" type="password" autocomplete="off" spellcheck="false">'
    + '<button id="connect" type="button">Connect</button>'
    + "<p>密钥只在此页面内存中用于签发短时 Peer JWT，不会保存。</p>"
    + disconnect
    + "<p>断开只清除此浏览器中的会话，不会删除已保存的信息。</p>"
    + script,
    script_nonce=nonce,
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
