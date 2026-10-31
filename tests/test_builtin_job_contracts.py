"""The artifact catalog remains usable without importing an application runtime."""

import subprocess
import sys


def test_catalog_import_needs_no_runtime_environment():
  result = subprocess.run(
    [
      sys.executable,
      "-c",
      """
import sys
from app.database_contract.profile import BUILTIN_JOB_TYPES
assert BUILTIN_JOB_TYPES
assert not {'sqlmodel', 'sqlalchemy', 'fastapi', 'app.settings', 'app.engine',
            'app.scheduler'} & sys.modules.keys()
""",
    ],
    capture_output=True,
    text=True,
    check=False,
  )
  assert result.returncode == 0, result.stderr
