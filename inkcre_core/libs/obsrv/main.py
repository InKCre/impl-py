"""Main observability setup."""

import logging
import sys
from .setting import ObsrvSetting

__all__ = ["get_logger"]

LOGGER = logging.getLogger("inkcre")


def setup_obsrv(config: ObsrvSetting) -> logging.Logger:
  """Setup observability components."""

  # Create logger
  global LOGGER
  LOGGER = logging.getLogger("inkcre")
  LOGGER.setLevel(logging.DEBUG)

  # Clear any existing handlers
  LOGGER.handlers.clear()

  # Console handler (always enabled)
  console_handler = logging.StreamHandler(sys.stdout)
  console_handler.setLevel(logging.INFO)
  console_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
  )
  console_handler.setFormatter(console_formatter)
  LOGGER.addHandler(console_handler)

  # Destination selection belongs to the bound Core, never this global logger.
  from .runtime import ObservationLogHandler

  LOGGER.addHandler(ObservationLogHandler(level=config.logging_backend_level))
  if config.logging_backend == "logtail":
    LOGGER.warning(
      "Legacy Logtail writer settings no longer select storage; configure "
      "inkcre.observability.backend and a supported read/write backend"
    )

  return LOGGER


def get_logger() -> logging.Logger:
  """Get the application logger instance.

  Returns:
      Logger instance for the application
  """
  global LOGGER
  return LOGGER


def start_obsrv() -> None:
  """Compatibility entry; the App starts its bound runtime after config admission."""


async def close_obsrv() -> None:
  from .telemetry import close_telemetry

  await close_telemetry()
  # Core.aclose owns its selected backend and drains it before closing the DB.
