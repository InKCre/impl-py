"""Verify the public x-monkey browser-form capability without a database connection."""

import os
from pathlib import Path
import sys

# App settings are evaluated during Extension imports; use inert local-only values here.
os.environ["DATABASE_URL"] = "postgresql://localhost/peer_handoff_test"
os.environ["JWT_SECRET"] = "0" * 32
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.schemas.peer import CorePeerConfig
from extensions.twitter.setup_flow import (
  TWITTER_SETUP_INBOUNDS,
  TWITTER_X_MONKEY_SESSION_CAPABILITY,
)


def main() -> None:
  https_config = CorePeerConfig(http_public_base_url="https://core.example/prefix")
  advertisements = tuple(
    inbound.advertise(https_config) for inbound in TWITTER_SETUP_INBOUNDS
  )
  handoff = next(
    item
    for item in advertisements
    if item and item.id == TWITTER_X_MONKEY_SESSION_CAPABILITY
  )
  assert handoff.inbound.protocol == "core.peer.protocol.browser-form.v1"
  assert handoff.inbound.parameters == {
    "method": "POST",
    "path": "/twitter/x-monkey/session",
    "credential_field": "token",
  }

  http_config = CorePeerConfig(http_public_base_url="http://core.example")
  assert all(
    item is None or item.id != TWITTER_X_MONKEY_SESSION_CAPABILITY
    for item in (inbound.advertise(http_config) for inbound in TWITTER_SETUP_INBOUNDS)
  )
  print("browser-form capability advertisement: ok; HTTPS-only; no database access")


if __name__ == "__main__":
  main()
