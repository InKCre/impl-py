"""B 站插件的 Python 读取 Distribution。"""

import pydantic

from app.business.extension import ExtensionBase


class BilibiliConfig(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")


class Extension(
  ExtensionBase[BilibiliConfig], ext_id="bilibili", config_cls=BilibiliConfig
):
  @classmethod
  def _init_resolvers(cls) -> None:
    from . import resolver as resolver_module  # noqa: F401


__all__ = ["BilibiliConfig", "Extension"]
