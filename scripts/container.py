"""Provider-neutral process contract for the OCI artifact."""

import asyncio
import os
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _web() -> int:
  port = os.getenv("PORT", "8000")
  if not port.isdigit():
    raise SystemExit("PORT must be an integer")

  from app.database_contract.readiness import check_database_contract

  retry_seconds = float(os.getenv("RUNTIME_BOOTSTRAP_RETRY_SECONDS", "5"))
  while True:
    readiness = check_database_contract()
    if readiness.ready:
      break
    print(f"Waiting for database contract: {readiness.reason}", flush=True)
    time.sleep(retry_seconds)

  from app.business.extension.main import EXTENSION_HOST

  from app.core import create_core

  async def provision() -> None:
    with create_core().bind() as core:
      try:
        await EXTENSION_HOST.provision_enabled_distributions()
      finally:
        await core.aclose()

  asyncio.run(provision())

  os.execvp(
    sys.executable,
    [sys.executable, "scripts/web.py", "--port", port],
  )
  return 0


def _database(args: list[str]) -> int:
  from scripts.database import main as database_main

  return database_main(args)


def _ready() -> int:
  from app.database_contract.readiness import check_database_contract

  result = check_database_contract()
  print(result.reason)
  return 0 if result.ready else 1


COMMANDS = {
  "web": _web,
  "ready": _ready,
}


def _resolve_command(args: list[str]) -> str | None:
  if len(args) >= 2 and args[0] == "db":
    return "db"
  if len(args) == 1 and args[0] in COMMANDS:
    return args[0]
  return None


def main(argv: list[str] | None = None) -> int:
  args = argv if argv is not None else sys.argv[1:]
  command = _resolve_command(args)
  if command is None:
    available = ", ".join([*sorted(COMMANDS), "db <command>"])
    print(f"usage: container.py <{available}>", file=sys.stderr)
    return 2
  if command == "db":
    return _database(args[1:])
  return COMMANDS[command]()


if __name__ == "__main__":
  raise SystemExit(main())
