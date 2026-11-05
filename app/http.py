"""Public HTTP address supplied by this Core's deployment configuration."""

from inkcre_core.business.peer import PeerManager


async def get_public_http_base_url() -> str | None:
  """Return the validated external base, preserving its path, or None if unset.

  Core-owned configuration remains stored on this Core's Peer row. Extensions
  consume this address without depending on that persistence layout or guessing
  a public URL from request headers or the listening socket.
  """
  return (await PeerManager.get_current_config_async()).http_public_base_url
