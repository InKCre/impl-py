"""Path projection for nested input contracts; validation stays with their owners."""

import contextlib
import typing

import jsonschema  # pyrefly: ignore[untyped-import]
import pydantic


@contextlib.contextmanager
def input_path(*prefix: str | int):
  """Attach a form nesting path to native errors without validating again."""
  try:
    yield
  except pydantic.ValidationError as error:
    details = [
      {**item, "loc": (*prefix, *item["loc"])} for item in error.errors(include_url=False)
    ]
    raise pydantic.ValidationError.from_exception_data(
      error.title, typing.cast(typing.Any, details)
    ) from error
  except jsonschema.ValidationError as error:
    raise jsonschema.ValidationError(
      error.message,
      validator=error.validator,
      validator_value=error.validator_value,
      instance=error.instance,
      schema=error.schema,
      path=(*prefix, *error.absolute_path),
      schema_path=error.absolute_schema_path,
    ) from error
