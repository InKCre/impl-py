import typing
import uuid

import pydantic
import sqlalchemy
import sqlalchemy.dialects.postgresql
import sqlmodel


ExtensionName: typing.TypeAlias = str
EXTENSION_NAME_PATTERN = (
  r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?/"
  r"[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])?$"
)
EXTENSION_SEMVER_PATTERN = (
  r"^(0|[1-9][0-9]*)[.](0|[1-9][0-9]*)[.](0|[1-9][0-9]*)"
  r"(-(0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
  r"([.](0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)


class InstallExtensionCommand(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  action: typing.Literal["install"]
  extension: ExtensionName
  version: str = pydantic.Field(pattern=EXTENSION_SEMVER_PATTERN)


class SelectExtensionCommand(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  action: typing.Literal["select"]
  extension: ExtensionName
  distribution: typing.Literal["python", "module_federation", "swift_bundled"]
  version: str = pydantic.Field(pattern=EXTENSION_SEMVER_PATTERN)


class ExtensionStatusCommand(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  action: typing.Literal["status"]
  extension: ExtensionName | None = None


class UnselectExtensionCommand(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  action: typing.Literal["unselect"]
  extension: ExtensionName


class EnableExtensionCommand(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  action: typing.Literal["enable"]
  extension: ExtensionName


class DisableExtensionCommand(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  action: typing.Literal["disable"]
  extension: ExtensionName


class PatchExtensionConfigCommand(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  action: typing.Literal["patch_config"]
  extension: ExtensionName
  patch: dict[str, typing.Any]


class ExtensionPeerStatusRow(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  name: ExtensionName
  peer_id: uuid.UUID
  distribution: typing.Literal["python", "module_federation", "swift_bundled"] | None
  version: str | None
  enabled: bool
  config_schema: dict[str, typing.Any] | None
  supported_distributions: tuple[
    typing.Literal["python", "module_federation", "swift_bundled"], ...
  ]
  loaded_version: str | None
  running: bool
  restart_required: bool
  operation: str | None
  error: str | None


class ExtensionPeerStatus(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  peer_id: uuid.UUID
  supported_distributions: tuple[
    typing.Literal["python", "module_federation", "swift_bundled"], ...
  ]
  extensions: tuple[ExtensionPeerStatusRow, ...]


class SelectExtensionInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  distribution: typing.Literal["python", "module_federation", "swift_bundled"]
  version: str = pydantic.Field(pattern=EXTENSION_SEMVER_PATTERN)


ExtensionManagementCommand: typing.TypeAlias = typing.Annotated[
  SelectExtensionCommand
  | ExtensionStatusCommand
  | UnselectExtensionCommand
  | EnableExtensionCommand
  | DisableExtensionCommand
  | PatchExtensionConfigCommand,
  pydantic.Field(discriminator="action"),
]


class ExtensionModel(sqlmodel.SQLModel, table=True):
  """Shared Extension identity, configuration, and producer-owned state."""

  __tablename__: str = "extensions"  # type: ignore
  __table_args__ = (
    sqlalchemy.CheckConstraint(
      f"name ~ '{EXTENSION_NAME_PATTERN}'",
      name="extensions_name_canonical",
    ),
    sqlalchemy.CheckConstraint(
      "jsonb_typeof(state) = 'object'",
      name="extensions_state_object",
    ),
  )

  name: ExtensionName = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, primary_key=True)
  )
  nickname: str | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  config: dict = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      server_default=sqlalchemy.text("'{}'::jsonb"),
      nullable=False,
    ),
  )
  state: dict = sqlmodel.Field(
    default_factory=dict,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB,
      server_default=sqlalchemy.text("'{}'::jsonb"),
      nullable=False,
    ),
  )


class ExtensionPeerParticipationModel(sqlmodel.SQLModel, table=True):
  """One Peer’s exact native distribution selection and enable intent."""

  __tablename__: str = "extension_peer_participations"  # type: ignore
  __table_args__ = (
    sqlalchemy.CheckConstraint(
      f"name ~ '{EXTENSION_NAME_PATTERN}'",
      name="extension_peer_participations_name_canonical",
    ),
    sqlalchemy.CheckConstraint(
      f"version ~ '{EXTENSION_SEMVER_PATTERN}'",
      name="extension_peer_participations_version_canonical",
    ),
    sqlalchemy.CheckConstraint(
      "distribution IS NULL OR distribution IN "
      "('python', 'module_federation', 'swift_bundled')",
      name="extension_peer_participations_distribution_valid",
    ),
    sqlalchemy.CheckConstraint(
      "config_schema IS NULL OR jsonb_typeof(config_schema) = 'object'",
      name="extension_peer_participations_config_schema_object",
    ),
    sqlalchemy.CheckConstraint(
      "distribution IS NOT NULL OR config_schema IS NULL",
      name="extension_peer_participations_unresolved_schema_null",
    ),
    sqlalchemy.ForeignKeyConstraint(
      ["name"], ["inkcre.extensions.name"], ondelete="CASCADE"
    ),
    sqlalchemy.ForeignKeyConstraint(["peer_id"], ["inkcre.peers.id"], ondelete="CASCADE"),
    sqlalchemy.PrimaryKeyConstraint(
      "name", "peer_id", name="extension_peer_participations_pkey"
    ),
  )

  name: ExtensionName = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  peer_id: uuid.UUID = sqlmodel.Field(
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.UUID(as_uuid=True), nullable=False
    )
  )
  distribution: str | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=True),
  )
  version: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  enabled: bool = sqlmodel.Field(
    default=False,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Boolean, nullable=False, server_default=sqlalchemy.false()
    ),
  )
  config_schema: dict | None = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.dialects.postgresql.JSONB(none_as_null=True), nullable=True
    ),
  )
