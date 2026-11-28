"""Core facade for exact Release resolution owned by the Extension Runtime."""

from inkcre_extension_runtime_core_py.contracts import (
  ExtensionReleaseDescriptor,
  PythonReleaseDescriptor,
  ReleaseState,
)
from inkcre_extension_runtime_core_py.release import (
  RegistryReleaseClient,
  require_python_association as _require_python_association,
  simple_project_and_index_urls,
  validate_coordinate,
)

from app.version import CORE_VERSION


def require_python_association(
  release: ExtensionReleaseDescriptor,
) -> PythonReleaseDescriptor:
  return _require_python_association(release, CORE_VERSION)


__all__ = [
  "ExtensionReleaseDescriptor",
  "PythonReleaseDescriptor",
  "RegistryReleaseClient",
  "ReleaseState",
  "require_python_association",
  "simple_project_and_index_urls",
  "validate_coordinate",
]
