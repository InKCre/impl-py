"""Reusable Agent definitions persisted as shared deployment facts."""

import datetime
import typing

import pydantic
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel

from inkcre_core.schemas.ai import AIModelID, ToolChoice


AgentID: typing.TypeAlias = int

_TOOL_CHOICE_ADAPTER = pydantic.TypeAdapter(ToolChoice | None)


def normalize_agent_tools(value: typing.Iterable[str]) -> tuple[str, ...]:
  """Validate and canonicalize Agent Tool IDs with set semantics."""
  tools = tuple(value)
  if any(not tool for tool in tools):
    raise ValueError("Agent Tool IDs must not be empty")
  if len(tools) != len(set(tools)):
    raise ValueError("Agent Tool IDs must be unique")
  return tuple(sorted(tools))


class AgentToolsType(sqlalchemy.TypeDecorator):
  """Round-trip canonical Agent Tool ID sets through PostgreSQL text arrays."""

  impl = sqlalchemy.dialects.postgresql.ARRAY(sqlalchemy.Text)
  cache_ok = True

  def process_bind_param(self, value, dialect):
    del dialect
    return list(normalize_agent_tools(value or ()))

  def process_result_value(self, value, dialect):
    del dialect
    return tuple(value or ())


class ToolChoiceType(sqlalchemy.TypeDecorator):
  """Round-trip the canonical nullable ToolChoice union through JSONB."""

  impl = sqlalchemy.dialects.postgresql.JSONB
  cache_ok = True

  def load_dialect_impl(self, dialect):
    return dialect.type_descriptor(sqlalchemy.dialects.postgresql.JSONB(none_as_null=True))

  def process_bind_param(self, value, dialect):
    del dialect
    choice = _TOOL_CHOICE_ADAPTER.validate_python(value)
    if isinstance(choice, pydantic.BaseModel):
      return choice.model_dump(mode="json")
    return choice

  def process_result_value(self, value, dialect):
    del dialect
    return _TOOL_CHOICE_ADAPTER.validate_python(value)


class AgentForm(pydantic.BaseModel):
  """Editable Agent definition, without execution or database-managed fields."""

  model_config = pydantic.ConfigDict(extra="forbid")

  name: str
  system_prompt: str
  model: AIModelID
  tools: tuple[str, ...] = ()
  tool_choice: ToolChoice | None = None
  max_model_calls_per_turn: int = pydantic.Field(gt=0)

  @pydantic.field_validator("tools")
  @classmethod
  def canonical_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
    return normalize_agent_tools(value)


class AgentUpdateForm(pydantic.BaseModel):
  """PATCH omission preserves a field; only tool_choice accepts explicit null."""

  model_config = pydantic.ConfigDict(extra="forbid")

  name: str = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]
  system_prompt: str = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]
  model: AIModelID = pydantic.Field(default=None)  # pyrefly: ignore[bad-assignment]
  tools: tuple[str, ...] = ()
  tool_choice: ToolChoice | None = None
  max_model_calls_per_turn: int = pydantic.Field(default=None, gt=0)  # pyrefly: ignore[bad-assignment]


class AgentDefinitionModel(sqlmodel.SQLModel, table=True):
  """One reusable system-prompt, model, Tool and turn-budget composition."""

  __tablename__ = "agents"  # type: ignore
  __table_args__ = (
    sqlalchemy.CheckConstraint(
      "max_model_calls_per_turn > 0",
      name="ck_agents_max_model_calls_per_turn_positive",
    ),
  )

  id: AgentID | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.Identity(),
      primary_key=True,
    ),
  )
  name: str = sqlmodel.Field(sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False))
  system_prompt: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  tools: tuple[str, ...] = sqlmodel.Field(
    default=(),
    sa_column=sqlalchemy.Column(
      AgentToolsType(),
      nullable=False,
      server_default=sqlalchemy.text("'{}'::text[]"),
    ),
  )
  tool_choice: ToolChoice | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(ToolChoiceType(), nullable=True),
  )
  model: AIModelID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.BigInteger,
      sqlalchemy.ForeignKey("ai_models.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=False,
    )
  )
  max_model_calls_per_turn: int = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Integer, nullable=False)
  )
  created_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )
  updated_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      nullable=False,
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )

  @pydantic.field_validator("tools", mode="before")
  @classmethod
  def canonical_tools(cls, value: typing.Iterable[str]) -> tuple[str, ...]:
    return normalize_agent_tools(value)

  @pydantic.field_validator("tool_choice", mode="before")
  @classmethod
  def canonical_tool_choice(cls, value: typing.Any) -> ToolChoice | None:
    return _TOOL_CHOICE_ADAPTER.validate_python(value)

  @pydantic.field_validator("max_model_calls_per_turn")
  @classmethod
  def positive_model_call_budget(cls, value: int) -> int:
    if value <= 0:
      raise ValueError("max_model_calls_per_turn must be positive")
    return value
