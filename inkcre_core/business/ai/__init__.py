"""AI registry facts and peer-local capability execution."""

from .contracts import (
  AICapabilityUnavailableError,
  AIDialectAdapter,
  AIExecutionRequirement,
  AIFeatureUnavailableError,
  AIInputUnavailableError,
  AIModelDisabledError,
  AIModelNotFoundError,
  AIOutputContractError,
  AIProviderDisabledError,
  AIProviderNotFoundError,
  DuplicateAIDialectRegistrationError,
  InvalidAIProviderConfigError,
  UnknownAIDialectError,
)
from .main import AIManager

# Import installed core dialects only after AIManager exists so decorators register.
from .dialects import (
  AlibabaModelStudioDialect,
  OpenAICompatibleConfig,
  OpenAICompatibleDialect,
)

__all__ = [
  "AICapabilityUnavailableError",
  "AIDialectAdapter",
  "AIExecutionRequirement",
  "AIFeatureUnavailableError",
  "AIInputUnavailableError",
  "AIManager",
  "AIModelDisabledError",
  "AIModelNotFoundError",
  "AIOutputContractError",
  "AIProviderDisabledError",
  "AIProviderNotFoundError",
  "AlibabaModelStudioDialect",
  "DuplicateAIDialectRegistrationError",
  "InvalidAIProviderConfigError",
  "OpenAICompatibleConfig",
  "OpenAICompatibleDialect",
  "UnknownAIDialectError",
]
