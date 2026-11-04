"""Persistence-neutral mechanics for model-owned configuration values."""

from collections.abc import Mapping
import typing

import pydantic


ConfigModelT = typing.TypeVar("ConfigModelT", bound=pydantic.BaseModel)


class ConfigContract(typing.Generic[ConfigModelT]):
  """Apply one Pydantic model as a complete configuration contract.

  This abstraction deliberately has no persistence, registry, key, or live-update
  behavior. Those consequences remain with the domain that owns the config.
  """

  def __init__(self, model: type[ConfigModelT]) -> None:
    self.model = model

  def validate(
    self,
    value: ConfigModelT | Mapping[str, typing.Any],
  ) -> ConfigModelT:
    """Validate new input; pass an already constructed target model through.

    Passing a model back through Pydantic can rerun its model validators.
    Typed callers have already crossed that boundary; persistence consumers may
    still use one native model construction to restore nested Python types.
    """
    if isinstance(value, self.model):
      return value
    return self.model.model_validate(value)

  def normalize(
    self,
    value: ConfigModelT | Mapping[str, typing.Any],
  ) -> dict[str, typing.Any]:
    """Return the complete value in JSON-compatible canonical form."""
    return self.validate(value).model_dump(mode="json")

  def prepare_patch(
    self,
    current: ConfigModelT | Mapping[str, typing.Any],
    patch: Mapping[str, typing.Any],
  ) -> ConfigModelT:
    """Shallow-merge a patch and validate the resulting complete value."""
    candidate = (
      current.model_dump(mode="json")
      if isinstance(current, pydantic.BaseModel)
      else dict(current)
    )
    candidate.update(patch)
    return self.validate(candidate)

  def json_schema(self) -> dict[str, typing.Any]:
    """Project the model's JSON Schema for UI or protocol discovery."""
    return self.model.model_json_schema()
