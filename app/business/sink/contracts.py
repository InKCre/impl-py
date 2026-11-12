"""Stable Agent-facing arguments for the built-in MCP Sink."""

import typing

import pydantic

RetrievalMode: typing.TypeAlias = typing.Literal["lexical", "semantic"]
ContentMode: typing.TypeAlias = typing.Literal["raw", "hydrated", "solved"]


class ResolverMethodCall(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  block: int
  method: str
  arguments: dict[str, typing.Any] = pydantic.Field(default_factory=dict)
