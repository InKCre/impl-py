"""Installed core AI dialect adapters."""

from .alibaba_model_studio import AlibabaModelStudioDialect
from .openai_compatible import OpenAICompatibleConfig, OpenAICompatibleDialect

__all__ = [
  "AlibabaModelStudioDialect",
  "OpenAICompatibleConfig",
  "OpenAICompatibleDialect",
]
