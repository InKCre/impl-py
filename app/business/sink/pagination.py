"""Scope-bound live keyset cursors for MCP metadata and condition queries."""

import base64
import binascii
import json
import typing

import pydantic

from inkcre_core.schemas.info_base.query import PostgresInteger


class _Cursor(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid")

  version: typing.Annotated[pydantic.StrictInt, pydantic.Field(ge=1, le=1)]
  scope: dict[str, pydantic.JsonValue]
  order: typing.Literal["id_asc"]
  key: pydantic.StrictStr | PostgresInteger


def page_after(cursor: str | None, scope: dict, *, numeric: bool) -> str | int | None:
  if cursor is None:
    return None
  try:
    decoded = _Cursor.model_validate_json(
      base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
    )
  except (ValueError, binascii.Error) as error:
    raise ValueError("Invalid pagination cursor") from error
  if (
    json.dumps(decoded.scope, sort_keys=True) != json.dumps(scope, sort_keys=True)
    or (type(decoded.key) is int) != numeric
  ):
    raise ValueError("Cursor does not match tool, branch or conditions")
  return decoded.key


def page_result(rows: list[dict], *, limit: int, scope: dict) -> dict:
  more = len(rows) > limit
  rows = rows[:limit]
  cursor = None
  if more:
    cursor = base64.urlsafe_b64encode(
      _Cursor(version=1, scope=scope, order="id_asc", key=rows[-1]["id"])
      .model_dump_json()
      .encode()
    ).decode()
  return {"results": rows, "next_cursor": cursor}
