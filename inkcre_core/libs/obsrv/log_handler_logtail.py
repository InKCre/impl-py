"""Logtail logging handler."""

import logging
from typing import Optional

from logtail import LogtailHandler as RealLogtailHandler


class LogtailHandler:
  """Wrapper for Logtail handler."""

  def __init__(self, source_token: Optional[str] = None, host: Optional[str] = None):
    self.source_token = source_token
    self.host = host
    self.handler = None

  def get_handler(self) -> Optional[logging.Handler]:
    """Get the logtail handler if available."""
    if not self.source_token:
      return None
    if self.handler is None:
      try:
        self.handler = RealLogtailHandler(source_token=self.source_token)
        if self.host:
          self.handler.host = self.host
        self.handler.setLevel(logging.INFO)
      except Exception:
        return None
    return self.handler
