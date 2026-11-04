"""InKCre SDK. Importing contracts never initializes application resources."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
  from .context import Core, CoreOptions, current_core

__all__ = ["Core", "CoreOptions", "current_core"]


def __getattr__(name: str):
  if name not in __all__:
    raise AttributeError(name)
  from . import context

  return getattr(context, name)
