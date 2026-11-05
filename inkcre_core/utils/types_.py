__all__ = ["Undefined", "_undefined"]

import enum


class Undefined(enum.Enum):
  _undefined = 0


_undefined = Undefined._undefined
