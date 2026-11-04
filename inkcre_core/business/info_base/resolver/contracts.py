"""Stable resolver IDs, projection controls, and typed contract failures."""

from typing import Literal, TypeAlias

import pydantic


TextProjectionContext: TypeAlias = Literal["default", "lexical"]


CoreResolverID: TypeAlias = Literal[
  "core.text.v1",
  "core.markdown.v1",
  "core.html.v1",
  "core.image.v1",
  "core.audio.v1",
  "core.video.v1",
  "core.pdf.v1",
  "core.epub.v1",
  "core.zip.v1",
  "core.file.v1",
]

CORE_RESOLVER_IDS: tuple[CoreResolverID, ...] = (
  "core.text.v1",
  "core.markdown.v1",
  "core.html.v1",
  "core.image.v1",
  "core.audio.v1",
  "core.video.v1",
  "core.pdf.v1",
  "core.epub.v1",
  "core.zip.v1",
  "core.file.v1",
)


class ResolverContractError(RuntimeError):
  """Base failure for resolver selection and capability contracts."""


class ResolverMethodInputError(pydantic.ValidationError):
  """Native input details, distinguishable from errors inside a content method."""


class UnknownResolverMethodError(ResolverContractError, ValueError):
  """An exact Resolver has no projected read method with this name."""


class UnknownResolverError(ResolverContractError):
  """No exact resolver decoder is registered for a persisted resolver ID."""

  def __init__(self, resolver_id: str):
    self.resolver_id = resolver_id
    super().__init__(f"Unknown resolver ID: {resolver_id}")


class UnknownDraftResolverError(ResolverContractError):
  """The selected exact Resolver has no graph-drafting capability."""

  def __init__(self, resolver_id: str):
    self.resolver_id = resolver_id
    super().__init__(f"Resolver is not available for graph drafting: {resolver_id}")


class DuplicateResolverRegistrationError(ResolverContractError):
  """Two different decoder classes claimed the same exact resolver ID."""

  def __init__(self, resolver_id: str, existing: type, attempted: type):
    self.resolver_id = resolver_id
    self.existing = existing
    self.attempted = attempted
    super().__init__(
      f"Resolver ID {resolver_id} is already registered by "
      f"{existing.__module__}.{existing.__qualname__}; "
      f"cannot register {attempted.__module__}.{attempted.__qualname__}"
    )


class UnsupportedResolverCapability(ResolverContractError):
  """The exact resolver contract does not provide one requested projection."""

  def __init__(self, resolver_id: str, capability: str):
    self.resolver_id = resolver_id
    self.capability = capability
    super().__init__(f"Resolver {resolver_id} does not support {capability}")


class ResolverContentError(ResolverContractError, ValueError):
  """Hydrated content does not satisfy its exact resolver contract."""

  def __init__(self, resolver_id: str, reason: str):
    self.resolver_id = resolver_id
    self.reason = reason
    super().__init__(f"Resolver {resolver_id} rejected content: {reason}")
