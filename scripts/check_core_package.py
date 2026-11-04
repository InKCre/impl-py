"""Consume the built SDK wheel outside the checkout, without application imports."""

import ast
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PROBE = """
import asyncio
import importlib.abc
import sys

class NoApplication(importlib.abc.MetaPathFinder):
  def find_spec(self, fullname, path=None, target=None):
    if fullname.split('.')[0] in {'app', 'fastapi', 'starlette', 'apscheduler'}:
      raise AssertionError(f"SDK imported application dependency: {fullname}")

sys.meta_path.insert(0, NoApplication())
from inkcre_core import Core
from inkcre_core.business.info_base import BlockService, submit_graph
from inkcre_core.schemas.info_base.main import GraphForm
from inkcre_core.business.peer import PeerManager
from inkcre_core.business.job import JobManager

async def main():
  core = Core('postgresql+psycopg://unused:x@127.0.0.1:1/unused')
  with core.bind():
    assert PeerManager.get_current_peer_ref() == core.options.peer_id
    assert not core.accepting_jobs
    assert not core.pending_jobs
  await core.aclose()

asyncio.run(main())
print('Independent wheel import and lifecycle passed; no database contacted')
"""


def main() -> None:
  wheels = list((ROOT / "dist").glob("inkcre_core-*.whl"))
  if len(wheels) != 1:
    raise SystemExit("Build exactly one current SDK wheel in dist/ first")
  with tempfile.TemporaryDirectory(prefix="inkcre-sdk-consumer-") as directory:
    with zipfile.ZipFile(wheels[0]) as wheel:
      assert all(
        name.startswith(("inkcre_core/", "inkcre_core-")) for name in wheel.namelist()
      ), "SDK wheel contains App source"
      for name in wheel.namelist():
        if not name.endswith(".py"):
          continue
        for node in ast.walk(ast.parse(wheel.read(name))):
          modules = []
          if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
          elif isinstance(node, ast.ImportFrom) and not node.level:
            modules = [node.module or ""]
          assert not any(
            module.split(".")[0] in {"app", "fastapi", "starlette", "apscheduler"}
            for module in modules
          ), f"Application dependency in SDK: {name}"
      wheel.extractall(directory)
    environment = {
      key: value
      for key, value in os.environ.items()
      if key not in {"PYTHONPATH", "DATABASE_URL", "JWT_SECRET", "INKCRE_ENV_FILE"}
    }
    subprocess.run(  # noqa: S603 - fixed probe in the current Python environment.
      [sys.executable, "-c", PROBE], cwd=directory, env=environment, check=True
    )


if __name__ == "__main__":
  main()
