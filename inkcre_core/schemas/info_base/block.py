import datetime
import typing
import sqlalchemy
import sqlmodel
from pydantic import PrivateAttr
from typing import Optional as Opt
from .storage import StorageID

ResolverType: typing.TypeAlias = str
"""Type of resolver.

Extension resolvers should follows `extensions.{extension_id}.{resolver_name}` pattern.
"""
BlockID: typing.TypeAlias = int
HydratedContent: typing.TypeAlias = str | bytes
ResolverTV = typing.TypeVar("ResolverTV", bound="ResolverType")
"""Resolver type variable"""


class BlockForm(sqlmodel.SQLModel):
  """Producer-owned values for creating one Block."""

  model_config = {"extra": "forbid"}

  storage: Opt[StorageID] = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.Integer,
      sqlalchemy.ForeignKey("storages.id", onupdate="CASCADE", ondelete="RESTRICT"),
      nullable=True,
    ),
  )
  resolver: ResolverType = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  content: str = sqlmodel.Field(
    sa_column=sqlalchemy.Column(sqlalchemy.Text, nullable=False)
  )
  """Content of the block.

  Block content stored as text in database.
  For runtime usage, use storage to get the raw content.
  """


class BlockModel(BlockForm, table=True):
  __tablename__ = "blocks"  # type: ignore

  id: Opt[BlockID] = sqlmodel.Field(
    sa_column=sqlmodel.Column(sqlmodel.Integer, primary_key=True, autoincrement=True),
    default=None,
  )
  created_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )
  updated_at: datetime.datetime = sqlmodel.Field(
    default=None,
    sa_column=sqlalchemy.Column(
      sqlalchemy.TIMESTAMP(timezone=True),
      server_default=sqlalchemy.text("CURRENT_TIMESTAMP"),
    ),
  )

  _hydrated_content: HydratedContent | None = PrivateAttr(default=None)
  _hydrated_content_source: tuple[StorageID | None, str] | None = PrivateAttr(default=None)

  async def get_hydrated_content(self, *, refresh: bool = False) -> HydratedContent:
    """Return inline content or bytes loaded through the configured storage.

    The cache belongs only to this block instance. It is keyed by the persisted
    storage pointer fields and is replaced when ``refresh`` is requested.
    """
    source = (self.storage, self.content)
    private_state = self.__pydantic_private__
    if private_state is None:
      private_state = {}
      object.__setattr__(self, "__pydantic_private__", private_state)
    if (
      not refresh
      and private_state.get("_hydrated_content_source") == source
      and private_state.get("_hydrated_content") is not None
    ):
      return typing.cast(HydratedContent, private_state["_hydrated_content"])

    if self.storage is None:
      hydrated_content: HydratedContent = self.content
    else:
      from inkcre_core.business.info_base.storage import StorageManager

      storage = await StorageManager.get_storage_async(self.storage)
      stored_content = await storage.get_raw_content(self.content)
      if not isinstance(stored_content, bytes):
        raise TypeError(
          f"Storage {storage.__class__.__name__} returned "
          f"{type(stored_content).__name__}; configured storage must return bytes"
        )
      hydrated_content = stored_content

    private_state["_hydrated_content"] = hydrated_content
    private_state["_hydrated_content_source"] = source
    return hydrated_content

  async def get_context_as_text(self) -> str | None:
    from inkcre_core.business.info_base.resolver import ResolverManager

    resolver = ResolverManager.get(self)
    return await resolver.get_text()
