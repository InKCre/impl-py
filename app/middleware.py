"""Middleware for logging and request tracking."""

import time
from typing import Callable
import uuid

from fastapi import HTTPException, Request, Response
import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from inkcre_core.auth import decode_peer_jwt, create_peer_jwt as sign_peer_jwt
from app.settings import settings
from inkcre_core.libs.obsrv.log_record import TRACE_ID
from inkcre_core.libs.obsrv.main import get_logger


SENSITIVE_QUERY_PARAMETERS = frozenset(
  {"access_token", "client_secret", "code", "refresh_token", "state", "token"}
)


def logged_query_params(request: Request) -> str:
  """Render query diagnostics without persisting OAuth or credential material."""
  return "&".join(
    f"{key}={'<redacted>' if key.lower() in SENSITIVE_QUERY_PARAMETERS else value}"
    for key, value in request.query_params.multi_items()
  )


def create_peer_jwt(
  secret: str, *, now: float | None = None, lifetime_seconds: int | None = None
) -> str:
  return sign_peer_jwt(
    secret,
    now=now,
    lifetime_seconds=settings.jwt_default_lifetime_seconds
    if lifetime_seconds is None
    else lifetime_seconds,
  )


def require_peer_jwt(request: Request) -> dict:
  """Require the canonical peer JWT for an explicitly protected route tree."""
  auth_header = request.headers.get("Authorization")
  if not auth_header:
    raise HTTPException(
      status_code=401,
      detail="Authorization header missing",
      headers={"WWW-Authenticate": "Bearer"},
    )
  if not auth_header.startswith("Bearer "):
    raise HTTPException(
      status_code=401,
      detail="Invalid authorization header format",
      headers={"WWW-Authenticate": "Bearer"},
    )

  try:
    return decode_peer_jwt(auth_header[7:], settings.jwt_secret)
  except jwt.exceptions.InvalidTokenError as error:
    raise HTTPException(
      status_code=401,
      detail="Invalid token",
      headers={"WWW-Authenticate": "Bearer"},
    ) from error


class LoggingMiddleware(BaseHTTPMiddleware):
  """Middleware for logging requests, responses, and exceptions with trace ID."""

  def __init__(self, app: ASGIApp):
    super().__init__(app)
    self.logger = get_logger()

  async def dispatch(self, request: Request, call_next: Callable) -> Response:
    """Process request and log with trace ID.

    Args:
        request: The incoming request
        call_next: Next middleware/handler in chain

    Returns:
        Response from the handler
    """
    # Generate trace ID for this request
    trace_id = f"fastapi.request.${str(uuid.uuid4())}"

    # Store trace_id in request state and context variable
    request.state.trace_id = trace_id
    TRACE_ID.set(trace_id)

    # Log request
    start_time = time.time()
    self.logger.debug(
      f"Request started: {request.method} {request.url.path}",
      extra={
        "method": request.method,
        "path": request.url.path,
        "query_params": logged_query_params(request),
      },
    )

    try:
      # Process request
      response = await call_next(request)

      # Calculate duration
      duration = time.time() - start_time

      # Log response
      self.logger.debug(
        f"Request completed: {request.method} {request.url.path}",
        extra={
          "status_code": response.status_code,
          "duration_ms": int(duration * 1000),
        },
      )

      # Add trace ID to response headers
      response.headers["X-trace-ID"] = trace_id

      return response

    except Exception as e:
      # Calculate duration
      duration = time.time() - start_time

      # Log exception
      self.logger.error(
        f"Request failed: {request.method} {request.url.path} - {str(e)}",
        extra={
          "error.type": type(e).__name__,
          "error.message": str(e),
          "duration_ms": int(duration * 1000),
        },
      )

      # Re-raise exception to be handled by FastAPI's exception handlers
      raise


class JWTMiddleware(BaseHTTPMiddleware):
  """Middleware for JWT authentication."""

  def __init__(self, app: ASGIApp):
    super().__init__(app)
    # JWT secret is required in Settings, so it will always be set
    self.jwt_secret = settings.jwt_secret

  async def dispatch(self, request: Request, call_next: Callable) -> Response:
    """Validate JWT token from Authorization header.

    Args:
        request: The incoming request
        call_next: Next middleware/handler in chain

    Returns:
        Response from the handler
    """
    # Health and API-description endpoints are platform-readable.
    if (
      request.url.path in {"/heartbeat", "/livez", "/readyz"}
      or request.url.path == "/docs"
      or request.url.path.startswith("/openapi.json")
    ):
      return await call_next(request)

    require_peer_jwt(request)

    # Proceed to next middleware/handler
    return await call_next(request)
