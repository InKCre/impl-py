"""Sink type and instance lifecycle contract."""

import typing

import fastapi
import pydantic

from inkcre_core.schemas.sink import SinkModel, SinkTypeID


ConfigT = typing.TypeVar("ConfigT", bound=pydantic.BaseModel)


class SinkBase(typing.Generic[ConfigT]):
  """One persisted Sink instance realized by the current Peer."""

  __sinktype__: typing.ClassVar[SinkTypeID]
  __configcls__: typing.ClassVar[type[pydantic.BaseModel]]
  __configschema__: typing.ClassVar[dict[str, typing.Any]]

  def __init_subclass__(
    cls,
    sink_type: SinkTypeID | None = None,
    config_cls: type[ConfigT] | None = None,
    **kwargs: typing.Any,
  ) -> None:
    super().__init_subclass__(**kwargs)
    if sink_type is None or config_cls is None:
      return
    cls.__sinktype__ = sink_type
    cls.__configcls__ = config_cls
    cls.__configschema__ = config_cls.model_json_schema()
    from .main import SinkManager

    SinkManager.register_sink_type(cls)

  def __init__(self, model: SinkModel) -> None:
    if model.id is None:
      raise ValueError("Sink must be persisted before it can run")
    self.model = model
    self.config = typing.cast(ConfigT, self.__configcls__.model_validate(model.config))

  async def on_start(self, app: fastapi.FastAPI) -> None:
    """Publish active effects for this exact instance."""
    del app

  async def on_close(self) -> None:
    """Withdraw active effects for this exact instance."""

  def update_config(self, value: ConfigT | dict[str, typing.Any]) -> ConfigT:
    """Replace the live validated config without restarting the instance."""
    self.config = typing.cast(
      ConfigT,
      value
      if isinstance(value, self.__configcls__)
      else self.__configcls__.model_validate(value),
    )
    return self.config
