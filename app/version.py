"""Core application and Extension Host SDK versions."""

from pathlib import Path
import tomllib

CORE_VERSION = "0.3.2"
APPLICATION_VERSION = tomllib.loads(
  (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["version"]
