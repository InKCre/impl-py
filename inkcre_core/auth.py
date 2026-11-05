"""Peer JWT encoding and validation without application configuration."""

import math
import time
import jwt
from .database_contract.constants import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER, JWT_ROLE


def decode_peer_jwt(
  token: str,
  secret: str,
  *,
  now: float | None = None,
) -> dict:
  """Validate the canonical peer JWT contract used by every HTTP surface."""
  claims = jwt.decode(
    token,
    secret,
    algorithms=[JWT_ALGORITHM],
    audience=JWT_AUDIENCE,
    issuer=JWT_ISSUER,
    options={
      "require": ["role", "iss", "aud", "iat", "exp"],
      "verify_exp": False,
      "verify_iat": False,
    },
  )
  issued_at = claims["iat"]
  expires_at = claims["exp"]
  valid_numeric_dates = (
    isinstance(issued_at, (int, float))
    and not isinstance(issued_at, bool)
    and isinstance(expires_at, (int, float))
    and not isinstance(expires_at, bool)
  )
  if not valid_numeric_dates:
    raise jwt.exceptions.InvalidTokenError("JWT claim contract mismatch")
  try:
    issued_at_number = float(issued_at)
    expires_at_number = float(expires_at)
  except OverflowError as error:
    raise jwt.exceptions.InvalidTokenError("JWT claim contract mismatch") from error
  current_epoch = time.time() if now is None else now
  if (
    claims["role"] != JWT_ROLE
    or not math.isfinite(issued_at_number)
    or not math.isfinite(expires_at_number)
    or issued_at_number > current_epoch + 60
    or expires_at_number <= current_epoch
    or expires_at_number <= issued_at_number
  ):
    raise jwt.exceptions.InvalidTokenError("JWT claim contract mismatch")
  return claims


def create_peer_jwt(
  secret: str,
  *,
  now: float | None = None,
  lifetime_seconds: int = 60,
) -> str:
  """Sign a Peer JWT; the default is client policy, not an admission limit."""
  if not secret:
    raise ValueError("Peer JWT secret is required")
  if lifetime_seconds <= 0:
    raise ValueError("Peer JWT lifetime must be positive")
  issued_at = int(time.time() if now is None else now)
  return jwt.encode(
    {
      "role": JWT_ROLE,
      "iss": JWT_ISSUER,
      "aud": JWT_AUDIENCE,
      "iat": issued_at,
      "exp": issued_at + lifetime_seconds,
    },
    secret,
    algorithm=JWT_ALGORITHM,
  )
