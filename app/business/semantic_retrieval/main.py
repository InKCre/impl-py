"""Legacy import; use inkcre_core for new consumers."""

from typing import TYPE_CHECKING
from importlib import import_module
import sys

sys.modules[__name__] = import_module("inkcre_core.business.semantic_retrieval.main")

if TYPE_CHECKING:
  from inkcre_core.business.semantic_retrieval.main import *
