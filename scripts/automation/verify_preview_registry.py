"""Admit the Python-only Registry facade as static Pages assets."""

from pathlib import Path
import sys


REQUIRED_FILES = {Path("_headers"), Path("_redirects"), Path("v2/extensions.json")}
ASSET_SUFFIXES = {
  "v2": {".json"},
  "simple": {".html"},
  "packages": {".whl", ".metadata"},
}


def verify_registry(root: Path) -> None:
  if root.is_symlink() or not root.is_dir():
    raise ValueError("preview Registry artifact directory is missing or invalid")

  files: set[Path] = set()
  for path in root.rglob("*"):
    relative = path.relative_to(root)
    area = relative.parts[0]
    if not path.is_symlink() and path.is_dir() and area in ASSET_SUFFIXES:
      continue
    is_asset = relative in REQUIRED_FILES or (
      len(relative.parts) > 1 and relative.suffix in ASSET_SUFFIXES.get(area, ())
    )
    if path.is_symlink() or not path.is_file() or not is_asset:
      raise ValueError(f"unsupported preview Registry entry: {str(relative)!r}")
    files.add(relative)

  missing = REQUIRED_FILES - files
  if missing:
    names = ", ".join(map(str, sorted(missing)))
    raise ValueError(f"missing preview Registry files: {names}")


if __name__ == "__main__":
  try:
    verify_registry(Path(sys.argv[1]))
  except ValueError as error:
    raise SystemExit(str(error)) from None
