"""Run the web service with a same-process restart after environment changes."""

import argparse
import asyncio
import os
from pathlib import Path
import sys

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.settings import settings
from app.access_log import access_log_config
from app.runtime import process_restart_requested
from run import api_app


def _arguments() -> argparse.Namespace:
  parser = argparse.ArgumentParser()
  parser.add_argument("--port", type=int, default=settings.port)
  return parser.parse_args()


async def _serve(port: int) -> None:
  config = uvicorn.Config(
    api_app, host="0.0.0.0", port=port, log_config=access_log_config()
  )
  server = uvicorn.Server(config)
  import run

  run._CURRENT_SERVER = server
  await server.serve()


def main() -> None:
  args = _arguments()
  asyncio.run(_serve(args.port))
  if process_restart_requested():
    os.execv(sys.executable, [sys.executable, *sys.argv])


if __name__ == "__main__":
  main()
