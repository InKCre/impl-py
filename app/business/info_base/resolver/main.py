import abc
from collections.abc import Collection
from dataclasses import dataclass
import inspect
import typing
from typing import Optional as Opt

import pydantic

from app.business.info_base.services import RelationService
from app.schemas.info_base.main import StarsGraphForm
from app.schemas.info_base.block import BlockForm, BlockID, ResolverType, BlockModel
from app.schemas.info_base.relation import RelationModel
from app.schemas.info_base.storage import StorageID

from app.persistence.info_base.repository import BlockRepository

from .contracts import (
  DuplicateResolverRegistrationError,
  TextProjectionContext,
  UnknownDraftResolverError,
  UnknownResolverError,
  UnknownResolverMethodError,
  ResolverMethodInputError,
)


@dataclass(frozen=True)
class ResolverDraftCapability:
  """One code-owned Resolver graph-drafting contract."""

  resolver: ResolverType
  description: str
  input_model: type[pydantic.BaseModel]
  resolver_cls: type["Resolver"]


@dataclass(frozen=True)
class ResolverMethodContract:
  """One typed public read method on a registered Resolver."""

  name: str
  description: str
  input_model: type[pydantic.BaseModel]

  @property
  def input_schema(self) -> dict[str, typing.Any]:
    return self.input_model.model_json_schema()


class ResolverManager:
  RESOLVER_CLS: dict[ResolverType, type["Resolver"]] = {}
  """Global resolver registry.

  Map ResolverType to Resolver class
  """

  @classmethod
  def register_resolver(cls, resolver_cls: type["Resolver"]) -> None:
    existing = cls.RESOLVER_CLS.get(resolver_cls.__rsotype__)
    if existing is resolver_cls:
      return
    if existing is not None:
      raise DuplicateResolverRegistrationError(
        resolver_cls.__rsotype__,
        existing,
        resolver_cls,
      )
    cls.RESOLVER_CLS[resolver_cls.__rsotype__] = resolver_cls

  @classmethod
  def get(cls, block: BlockModel) -> "Resolver":
    """Create resolver instance from block."""
    try:
      resolver_cls = cls.RESOLVER_CLS[block.resolver]
    except KeyError as error:
      raise UnknownResolverError(block.resolver) from error
    return resolver_cls(block)

  @classmethod
  def get_draft_capabilities(cls) -> tuple[ResolverDraftCapability, ...]:
    """Snapshot currently registered Resolvers that can draft rooted graphs."""
    capabilities = (
      ResolverDraftCapability(
        resolver=resolver_id,
        description=resolver_cls.draft_description,
        input_model=resolver_cls.draft_input_model,
        resolver_cls=resolver_cls,
      )
      for resolver_id, resolver_cls in cls.RESOLVER_CLS.items()
      if resolver_cls.draft_description is not None
      and resolver_cls.draft_input_model is not None
    )
    return tuple(sorted(capabilities, key=lambda capability: capability.resolver))

  @classmethod
  def get_draft_capability(
    cls,
    resolver: ResolverType,
  ) -> ResolverDraftCapability:
    """Select one exact graph-drafting contract without fallback."""
    for capability in cls.get_draft_capabilities():
      if capability.resolver == resolver:
        return capability
    raise UnknownDraftResolverError(resolver)

  @classmethod
  def get_method_contracts(
    cls,
    resolver: ResolverType,
  ) -> tuple[ResolverMethodContract, ...]:
    """Discover typed public read methods on one registered Resolver."""
    resolver_cls = cls.RESOLVER_CLS.get(resolver)
    if resolver_cls is None:
      return ()
    return cls._method_contracts(resolver_cls)

  @classmethod
  def get_common_method_contracts(cls) -> tuple[ResolverMethodContract, ...]:
    """Common Resolver reads, available without per-type discovery."""
    return cls._method_contracts(Resolver)

  @staticmethod
  def _method_contracts(
    resolver_cls: type["Resolver"],
  ) -> tuple[ResolverMethodContract, ...]:
    contracts: list[ResolverMethodContract] = []
    for name, function in inspect.getmembers(resolver_cls, predicate=inspect.isfunction):
      if name.startswith("_") or not name.startswith(("get_", "read_")):
        continue
      try:
        signature = inspect.signature(function, eval_str=True)
        fields: dict[str, tuple[typing.Any, typing.Any]] = {}
        for parameter in signature.parameters.values():
          if parameter.name == "self":
            continue
          if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            raise TypeError("Variadic Resolver methods are not projectable")
          if parameter.annotation is inspect.Parameter.empty:
            raise TypeError("Resolver method parameters must be typed")
          default = (
            ... if parameter.default is inspect.Parameter.empty else parameter.default
          )
          annotation = parameter.annotation
          if typing.get_origin(annotation) is Collection:
            item_type = typing.get_args(annotation)[0]
            annotation = tuple[item_type, ...]
          fields[parameter.name] = (annotation, default)
        input_model = typing.cast(typing.Any, pydantic.create_model)(
          f"{resolver_cls.__name__}_{name}_Arguments",
          __config__=pydantic.ConfigDict(extra="forbid"),
          **fields,
        )
        input_model.model_json_schema()
      except (NameError, TypeError, pydantic.PydanticSchemaGenerationError):
        continue
      contracts.append(
        ResolverMethodContract(
          name=name,
          description=inspect.getdoc(function) or name.replace("_", " "),
          input_model=input_model,
        )
      )
    return tuple(contracts)

  @classmethod
  def get_method_contract(
    cls,
    resolver: ResolverType,
    name: str,
  ) -> ResolverMethodContract | None:
    return next(
      (
        contract for contract in cls.get_method_contracts(resolver) if contract.name == name
      ),
      None,
    )

  @classmethod
  async def invoke_method(
    cls,
    block: BlockModel,
    name: str,
    arguments: dict[str, typing.Any],
  ) -> typing.Any:
    """Validate and invoke one projected read method on an exact Block Resolver."""
    resolver = cls.get(block)
    contract = cls.get_method_contract(block.resolver, name)
    if contract is None:
      raise UnknownResolverMethodError(
        f"Resolver {block.resolver!r} has no method {name!r}"
      )
    try:
      validated = contract.input_model.model_validate(arguments)
    except pydantic.ValidationError as error:
      raise ResolverMethodInputError.from_exception_data(
        error.title, typing.cast(typing.Any, error.errors(include_url=False))
      ) from error
    # Keep nested Python values typed when calling the annotated domain method;
    # model_dump would convert nested input models back into dictionaries.
    value = getattr(resolver, name)(
      **{field: getattr(validated, field) for field in contract.input_model.model_fields}
    )
    return await value if inspect.isawaitable(value) else value

  @classmethod
  def match_media_type(cls, media_type: str | None) -> ResolverType | None:
    """Map one specific media type to an installed exact core resolver ID.

    Evidence precedence and generic/file fallback remain extension-owned.
    """
    if media_type is None:
      return None
    normalized = media_type.partition(";")[0].strip().lower()
    if not normalized or normalized in {
      "application/octet-stream",
      "binary/octet-stream",
      "application/binary",
    }:
      return None

    exact = {
      "text/plain": "core.text.v1",
      "text/markdown": "core.markdown.v1",
      "text/html": "core.html.v1",
      "application/xhtml+xml": "core.html.v1",
      "application/pdf": "core.pdf.v1",
      "application/epub+zip": "core.epub.v1",
      "application/zip": "core.zip.v1",
      "application/x-zip-compressed": "core.zip.v1",
    }
    resolver_id = exact.get(normalized)
    if resolver_id is None:
      family = normalized.partition("/")[0]
      resolver_id = {
        "image": "core.image.v1",
        "audio": "core.audio.v1",
        "video": "core.video.v1",
      }.get(family)
    return resolver_id if resolver_id in cls.RESOLVER_CLS else None


SolvedContentTV = typing.TypeVar("SolvedContentTV")
RawContentTV = typing.TypeVar("RawContentTV")
_UNSET = object()


class Resolver(abc.ABC, typing.Generic[SolvedContentTV, RawContentTV]):
  """Resolver resolves a star graph (a block and its direct relations)

  :tparam SolvedContentTV: The type of the solved content
  :tparam RawContentTV: The type of the raw content
  """

  __rsotype__: ResolverType
  """Resolver type
  
  Extension resolvers should be namespaced and versioned, e.g.,
  `extensions.twitter.tweet.v1`.
  """

  draft_description: typing.ClassVar[str | None] = None
  draft_input_model: typing.ClassVar[type[pydantic.BaseModel] | None] = None

  def __init_subclass__(cls, rso_type: str, **kwargs) -> None:
    cls.__rsotype__ = rso_type
    ResolverManager.register_resolver(cls)
    return super().__init_subclass__(**kwargs)

  def __init__(self, block: BlockModel, relations: Opt[tuple[RelationModel, ...]] = None):
    """Should never override __init__ in subclasses, use __post_init__ instead.

    :param block: Block to resolve.
    :param relations: Relations of the block.
    """
    self._block = block
    self.__relations: dict[tuple[bool, bool], tuple[RelationModel, ...]] = {}
    if relations is not None:
      self.__relations[(True, True)] = relations
    self.__solved_content: SolvedContentTV | object = _UNSET
    """Solved content is the content the resolver really works with,
    commonly from raw content.
    """
    inline_content = (
      typing.cast(RawContentTV, self._block.content)
      if self._block.storage is None
      else None
    )
    self.__post_init__(inline_content)

  def __post_init__(self, raw_content: Opt[RawContentTV] = None) -> None:
    """Subclass post-initialization hook.

    It's suggest to set __solved_content here if possible:
    ```python
    async def __post_init__(self, raw_content):
      if raw_content is not None:
        ... # anyhow from raw_content
        self.set_solved_content(solved_content)
    ```
    """
    ...

  @property
  def block_id(self) -> BlockID:
    """Get the block ID."""
    return typing.cast(BlockID, self._block.id)

  async def get_raw_content(
    self,
    *,
    refresh: typing.Annotated[
      bool, pydantic.Field(description="Reread current content.")
    ] = False,
  ) -> RawContentTV:
    """Read hydrated content: text or bytes, not a storage pointer."""
    return typing.cast(
      RawContentTV,
      await self._block.get_hydrated_content(refresh=refresh),
    )

  async def get_transfer_url(self) -> str | None:
    """Get a content transfer URL when available."""
    if self._block.storage is None:
      return None
    from app.business.info_base.storage import StorageManager

    storage = await StorageManager.get_storage_async(self._block.storage)
    return storage.get_transfer_url(self._block.content)

  async def get_solved_content(
    self,
    *,
    refresh: typing.Annotated[
      bool, pydantic.Field(description="Reread current content.")
    ] = False,
    materialize_missing: typing.Annotated[
      bool, pydantic.Field(description="Allow creation of missing derived information.")
    ] = True,
  ) -> SolvedContentTV:
    """Read the Resolver's typed interpretation of content."""
    if refresh or self.__solved_content is _UNSET:
      self.__solved_content = await self._get_solved_content(
        refresh=refresh,
        materialize_missing=materialize_missing,
      )
    return typing.cast(SolvedContentTV, self.__solved_content)

  async def _get_solved_content(
    self,
    *,
    refresh: bool = False,
    materialize_missing: bool = True,
  ) -> SolvedContentTV:
    """Get the solved content (non-cache).

    Description:
      When you say "will be resolved from <content> <in/out> relations",
      it means you acquire the resolver of the relation's other side block,
      and use its solved content.
    """
    del materialize_missing
    return typing.cast(
      SolvedContentTV,
      await self.get_raw_content(refresh=refresh),
    )

  def set_solved_content(self, content: SolvedContentTV) -> None:
    self.__solved_content = content

  async def get_relations(
    self,
    *,
    include_in: typing.Annotated[
      bool, pydantic.Field(description="Include relations pointing to this Block.")
    ] = True,
    include_out: typing.Annotated[
      bool, pydantic.Field(description="Include relations pointing from this Block.")
    ] = True,
    refresh: bool = False,
  ) -> tuple[RelationModel, ...]:
    """Read direct relations of this Block."""
    key = (include_in, include_out)
    if refresh or key not in self.__relations:
      all_relations = None if refresh else self.__relations.get((True, True))
      if all_relations is not None:
        self.__relations[key] = tuple(
          relation
          for relation in all_relations
          if (include_in and relation.to_ == self.block_id)
          or (include_out and relation.from_ == self.block_id)
        )
      else:
        self.__relations[key] = await RelationService.get(
          block_id=self.block_id,
          include_in=include_in,
          include_out=include_out,
        )
    return self.__relations[key]

  @classmethod
  # @abc.abstractmethod TODO
  def create_block(cls, content, storage: Opt[StorageID] = None) -> BlockForm: ...

  @classmethod
  # @abc.abstractmethod TODO
  def create_graph(cls, *args, **kwargs) -> StarsGraphForm: ...

  @abc.abstractmethod
  async def get_text(
    self,
    *,
    context: typing.Annotated[
      TextProjectionContext,
      pydantic.Field(description="Lexical projection is Block-local and non-recursive."),
    ] = "default",
    refresh: typing.Annotated[
      bool, pydantic.Field(description="Reread current content.")
    ] = False,
    materialize_missing: typing.Annotated[
      bool, pydantic.Field(description="Allow creation of missing derived information.")
    ] = True,
  ) -> str | None:
    """Read a text projection; unsupported, absent and empty are distinct."""
    ...

  @abc.abstractmethod
  async def get_label(self, *, refresh: bool = False) -> str:
    """Read a concise label for this Block."""
    ...

  async def get_existing_async(self, blocks: BlockRepository) -> Opt[BlockModel]:
    """Reconcile the exact resolver/content identity in the caller's transaction."""
    return await blocks.find_content(self._block.resolver, self._block.content)
