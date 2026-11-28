"""Keep request queries out of the process's Uvicorn access logs."""

from copy import deepcopy
import logging
from typing import Any, cast

from uvicorn.config import LOGGING_CONFIG


class QuerylessAccessFilter(logging.Filter):
  def filter(self, record: logging.LogRecord) -> bool:
    # Uvicorn's h11 and httptools protocols emit these five arguments. Keep the
    # record contract covered when upgrading Uvicorn, before any handler formats it.
    client, method, target, version, status = cast(
      tuple[str, str, str, str, int], record.args
    )
    record.args = (client, method, target.partition("?")[0], version, status)
    return True


def access_log_config() -> dict[str, Any]:
  """Return an independent Uvicorn config without changing application logging."""
  config = deepcopy(LOGGING_CONFIG)
  config["filters"] = {"queryless_access": {"()": QuerylessAccessFilter}}
  config["loggers"]["uvicorn.access"]["filters"] = ["queryless_access"]
  return config
