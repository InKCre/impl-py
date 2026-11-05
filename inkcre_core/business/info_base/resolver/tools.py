"""Agent Tool controller for typed Resolver method discovery and invocation."""

import typing

import pydantic

from inkcre_core.business.agent import AgentManager
from inkcre_core.business.agent.projection import project_json
from inkcre_core.business.info_base.services import BlockService
from inkcre_core.schemas.ai import JSONValue
from inkcre_core.schemas.info_base.block import BlockID, ResolverType
from inkcre_core.libs.obsrv.telemetry import emit_event

from .main import ResolverManager


RESOLVER_TOOL = "resolver"


class ResolverMethodCall(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  block_id: BlockID
  method: str
  arguments: dict[str, JSONValue] = pydantic.Field(default_factory=dict)


class ResolverDescribeInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  action: typing.Literal["describe"]
  resolver_types: tuple[ResolverType, ...] = ()
  block_ids: tuple[BlockID, ...] = ()
  calls: tuple[ResolverMethodCall, ...] = pydantic.Field(default=(), max_length=0)


class ResolverInvokeInput(pydantic.BaseModel):
  model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

  action: typing.Literal["invoke"]
  resolver_types: tuple[ResolverType, ...] = pydantic.Field(default=(), max_length=0)
  block_ids: tuple[BlockID, ...] = pydantic.Field(default=(), max_length=0)
  calls: tuple[ResolverMethodCall, ...] = pydantic.Field(min_length=1, max_length=20)


class ResolverMetaToolInput(
  pydantic.RootModel[
    typing.Annotated[
      ResolverDescribeInput | ResolverInvokeInput,
      pydantic.Field(discriminator="action"),
    ]
  ]
):
  pass


def _resolver_input_model() -> type[pydantic.BaseModel]:
  common = ResolverManager.get_common_method_contracts()
  variants: list[type[pydantic.BaseModel]] = []
  contracts = list(common)
  common_names = {item.name for item in common}
  for resolver_type in ResolverManager.RESOLVER_CLS:
    contracts.extend(
      contract
      for contract in ResolverManager.get_method_contracts(resolver_type)
      if contract.name in common_names
    )
  seen: set[tuple] = set()
  for contract in contracts:
    signature = (
      contract.name,
      tuple(
        (name, repr(field.annotation), repr(field.default), repr(field.metadata))
        for name, field in contract.input_model.model_fields.items()
      ),
    )
    if signature in seen:
      continue
    seen.add(signature)
    variants.append(
      pydantic.create_model(
        f"{contract.name}_Call_{len(variants)}",
        __config__=pydantic.ConfigDict(extra="forbid"),
        block_id=(int, ...),
        method=(
          typing.cast(typing.Any, typing.Literal)[contract.name],
          pydantic.Field(description=contract.description),
        ),
        arguments=(
          contract.input_model,
          ...
          if any(
            field.is_required() for field in contract.input_model.model_fields.values()
          )
          else pydantic.Field(default_factory=contract.input_model),
        ),
      )
    )

  variants.append(
    pydantic.create_model(
      "ExtraMethodCall",
      __base__=ResolverMethodCall,
      method=(
        str,
        pydantic.Field(
          json_schema_extra={"not": {"enum": [contract.name for contract in common]}}
        ),
      ),
    )
  )
  call_type = typing.cast(typing.Any, typing.Union)[tuple(variants)]
  invoke = pydantic.create_model(
    "BoundResolverInvokeInput",
    __base__=ResolverInvokeInput,
    calls=(tuple[call_type, ...], pydantic.Field(min_length=1, max_length=20)),
  )
  provider_envelope = pydantic.create_model(
    "ResolverEnvelope",
    __base__=ResolverDescribeInput,
    action=(typing.Literal["describe", "invoke"], ...),
    calls=(tuple[call_type, ...], pydantic.Field(default=(), max_length=20)),
  )
  method_contract = pydantic.RootModel[
    typing.Annotated[ResolverDescribeInput | invoke, pydantic.Field(discriminator="action")]
  ]

  # Runtime validates the dispatch envelope, then the selected Resolver validates
  # each call's arguments. One invalid method argument must not reject the batch.
  # The richer method schema below guides the model without changing that boundary.
  class BoundResolverInput(ResolverMetaToolInput):
    @classmethod
    def model_json_schema(cls, *args, **kwargs) -> dict[str, typing.Any]:
      method_schema = method_contract.model_json_schema(*args, **kwargs)
      # Some providers infer parameter types only from top-level properties.
      # Expose the wider envelope there while retaining the union's oneOf,
      # discriminator and $defs so describe/invoke keep their distinct contracts.
      provider_schema = provider_envelope.model_json_schema(*args, **kwargs)
      method_schema.update(type="object", properties=provider_schema["properties"])
      method_schema.setdefault("$defs", {}).update(provider_schema.get("$defs", {}))
      return method_schema

  return BoundResolverInput


@AgentManager.tool(
  RESOLVER_TOOL,
  input_model_factory=_resolver_input_model,
  description="Describe or invoke public typed read methods on exact Block Resolvers.",
)
async def resolver(input: ResolverMetaToolInput) -> JSONValue:
  request = input.root
  if request.action == "describe":
    found = await BlockService.get_many(request.block_ids)
    resolver_ids = set(request.resolver_types)
    resolver_ids.update(block.resolver for block in found)
    if not request.block_ids and not request.resolver_types:
      resolver_ids.update(ResolverManager.RESOLVER_CLS)
    return typing.cast(
      JSONValue,
      {
        "results": [
          {
            "resolver": resolver_id,
            "methods": [
              {
                "name": contract.name,
                "description": contract.description,
                "input_schema": contract.input_schema,
              }
              for contract in ResolverManager.get_method_contracts(resolver_id)
            ],
          }
          for resolver_id in sorted(resolver_ids)
          if resolver_id in ResolverManager.RESOLVER_CLS
        ],
        "missing_blocks": sorted(set(request.block_ids) - {block.id for block in found}),
        "missing_resolvers": sorted(
          resolver_id
          for resolver_id in resolver_ids
          if resolver_id not in ResolverManager.RESOLVER_CLS
        ),
      },
    )

  results: list[JSONValue] = []
  for index, call in enumerate(request.calls):
    block = await BlockService.get(call.block_id)
    if block is None:
      results.append(
        {
          "index": index,
          "block_id": call.block_id,
          "method": call.method,
          "error": "not_found",
        }
      )
      continue
    contract = ResolverManager.get_method_contract(block.resolver, call.method)
    if block.resolver not in ResolverManager.RESOLVER_CLS:
      results.append(
        {
          "index": index,
          "block_id": call.block_id,
          "method": call.method,
          "error": "resolver_unavailable",
          "message": f"Resolver {block.resolver!r} is not registered.",
        }
      )
      continue
    if contract is None:
      results.append(
        {
          "index": index,
          "block_id": call.block_id,
          "method": call.method,
          "error": "method_unavailable",
          "message": "Method does not exist; use describe for available method contracts.",
          "available_methods": [
            item.name for item in ResolverManager.get_method_contracts(block.resolver)
          ],
        }
      )
      continue
    try:
      value = await ResolverManager.invoke_method(
        block,
        call.method,
        call.arguments.model_dump()
        if isinstance(call.arguments, pydantic.BaseModel)
        else typing.cast(dict[str, typing.Any], call.arguments),
      )
      projected = project_json(value)
    except pydantic.ValidationError as error:
      results.append(
        typing.cast(
          JSONValue,
          {
            "index": index,
            "block_id": call.block_id,
            "method": call.method,
            "error": "invalid_arguments",
            "fields": error.errors(
              include_url=False, include_context=False, include_input=False
            ),
            "input_schema": contract.input_schema,
          },
        )
      )
    except Exception as error:
      results.append(
        {
          "index": index,
          "block_id": call.block_id,
          "method": call.method,
          "error": type(error).__name__,
          "message": str(error),
        }
      )
    else:
      emit_event(
        "inkcre.entity.read",
        {
          "inkcre.entity.read.kind": "resolved",
          "inkcre.entity.block_ids": (call.block_id,),
          "inkcre.entity.read.count": 1,
        },
      )
      results.append(
        {
          "index": index,
          "block_id": call.block_id,
          "method": call.method,
          "result": projected,
        }
      )
  return typing.cast(JSONValue, {"results": results})
