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

  # Add backend-specific handlers
  backend = config.logging_backend
  if backend == "logtail":
    from .log_handler_logtail import LogtailHandler

    logtail_wrapper = LogtailHandler(
      source_token=config.logtail_source_token,
      host=config.logtail_host,
    )
    handler = logtail_wrapper.get_handler()
    if handler:
      LOGGER.addHandler(handler)
      LOGGER.info("Logtail logging enabled")
    else:
      LOGGER.warning("Logtail logging configured but not available")
  elif backend == "postgresql":
    from .log_handler_postgresql import PostgreSQLHandler

    pg_handler = PostgreSQLHandler(level=config.logging_backend_level)
    LOGGER.addHandler(pg_handler)
    LOGGER.info("PostgreSQL logging enabled")
  elif backend not in (None, "", "none"):
    LOGGER.warning(f"Unknown logging backend: {backend}")

  return LOGGER


def get_logger() -> logging.Logger:
  """Get the application logger instance.

  Returns:
      Logger instance for the application
  """
  global LOGGER
  return LOGGER


def start_obsrv() -> None:
  from .log_handler_postgresql import PostgreSQLHandler

  for handler in LOGGER.handlers:
    if isinstance(handler, PostgreSQLHandler):
      handler.start()


async def close_obsrv() -> None:
  from .log_handler_postgresql import PostgreSQLHandler
  from .telemetry import close_telemetry

  await close_telemetry()

  for handler in LOGGER.handlers:
    if isinstance(handler, PostgreSQLHandler):
      await handler.aclose()
