"""Block-local lexical projection maintenance and exact ranked retrieval."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import logging
import typing

import pydantic

from inkcre_core.business.info_base.resolver import (
  ResolverManager,
  UnknownResolverError,
  UnsupportedResolverCapability,
)
from inkcre_core.business.peer import PeerManager
from inkcre_core.persistence.lexical_retrieval.uow import lexical_uow
from inkcre_core.schemas.info_base.block import BlockModel
from inkcre_core.schemas.lexical_retrieval import (
  LexicalEvidence,
  LexicalMaintenanceDiagnostic,
  LexicalMaintenanceOptions,
  LexicalMaintenanceReport,
  LexicalRetrievalMatch,
  LexicalRetrievalRequest,
  LexicalRetrievalResult,
)
from inkcre_core.schemas.peer import PeerProtocolRequest, PeerProtocolResponse, PeerRef


logger = logging.getLogger(__name__)

LEXICAL_RETRIEVAL_CAPABILITY = "core.feature_retrieval.lexical.v1"
_EXCERPT_LIMIT = 280


class LexicalRetrievalError(RuntimeError):
  pass


class LexicalRetrievalDelegationError(LexicalRetrievalError):
  pass


class _ProjectionUnavailable(Exception):
  pass


@dataclass(frozen=True)
class _Projection:
  block: int
  label: str
  text: str | None


@dataclass
class _ReportBuilder:
  diagnostic_limit: int
  indexed: int = 0
  unavailable: int = 0
  failed: int = 0
  diagnostics: list[LexicalMaintenanceDiagnostic] | None = None

  def __post_init__(self) -> None:
    self.diagnostics = []

  def record(
    self,
    block: int,
    outcome: typing.Literal["unavailable", "failed"],
    reason: str,
  ) -> None:
    if outcome == "unavailable":
      self.unavailable += 1
    else:
      self.failed += 1
    diagnostics = typing.cast(list[LexicalMaintenanceDiagnostic], self.diagnostics)
    if len(diagnostics) < self.diagnostic_limit:
      diagnostics.append(
        LexicalMaintenanceDiagnostic(block=block, outcome=outcome, reason=reason)
      )

  def build(self) -> LexicalMaintenanceReport:
    return LexicalMaintenanceReport(
      indexed=self.indexed,
      unavailable=self.unavailable,
      failed=self.failed,
      diagnostics=tuple(self.diagnostics or ()),
    )


class LexicalRetrievalManager:
  """Sole owner of lexical records, maintenance, ranking, and delegation."""

  @classmethod
  async def retrieve(
    cls,
    query: str,
    limit: int = 20,
    *,
    route_to_peer: PeerRef | None = None,
  ) -> LexicalRetrievalResult:
    request = LexicalRetrievalRequest(query=query, limit=limit)
    if route_to_peer is None or route_to_peer == PeerManager.get_current_peer_ref():
      return await cls.retrieve_local(request.query, request.limit)

    payload = PeerProtocolRequest(
      body=typing.cast(typing.Any, request.model_dump(mode="json"))
    )
    result = await PeerManager.delegate(
      LEXICAL_RETRIEVAL_CAPABILITY,
      typing.cast(typing.Any, payload.model_dump(mode="json", exclude_unset=True)),
      route_to_peer=route_to_peer,
    )
    try:
      response = PeerProtocolResponse.model_validate(result)
      if response.status != 200 or "body" not in response.model_fields_set:
        raise LexicalRetrievalDelegationError(
          f"Lexical retrieval Peer returned HTTP {response.status}"
        )
      return LexicalRetrievalResult.model_validate(response.body)
    except pydantic.ValidationError as error:
      raise LexicalRetrievalDelegationError(
        "Lexical retrieval Peer returned an invalid response"
      ) from error

  @classmethod
  async def retrieve_local(cls, query: str, limit: int = 20) -> LexicalRetrievalResult:
    request = LexicalRetrievalRequest(query=query, limit=limit)
    normalized = request.query.strip()
    async with lexical_uow() as repository:
      rows = await repository.search(normalized, request.limit)

    return LexicalRetrievalResult(
      matches=tuple(
        LexicalRetrievalMatch(
          block=block,
          label=record.label,
          excerpt=cls._excerpt(record.label, record.text, normalized),
          evidence=typing.cast(LexicalEvidence, evidence_value),
          rank=float(rank_value),
        )
        for block, record, evidence_value, rank_value in rows
      )
    )

  @classmethod
  async def maintain(
    cls,
    options: LexicalMaintenanceOptions | None = None,
  ) -> LexicalMaintenanceReport:
    return await cls._maintain(
      options or LexicalMaintenanceOptions(),
      rebuild_cutoff=None,
    )

  @classmethod
  async def rebuild(
    cls,
    options: LexicalMaintenanceOptions | None = None,
  ) -> LexicalMaintenanceReport:
    async with lexical_uow() as repository:
      cutoff = await repository.database_now()
    return await cls._maintain(
      options or LexicalMaintenanceOptions(),
      rebuild_cutoff=cutoff,
    )

  @classmethod
  async def _maintain(
    cls,
    options: LexicalMaintenanceOptions,
    *,
    rebuild_cutoff: datetime.datetime | None,
  ) -> LexicalMaintenanceReport:
    report = _ReportBuilder(options.diagnostic_limit)
    cursor = 0
    processed = 0
    while processed < options.max_records:
      async with lexical_uow() as repository:
        blocks, next_cursor, exhausted = await repository.candidate_page(
          cursor,
          min(options.scan_page_size, options.max_records - processed),
          rebuild_cutoff,
        )
      cursor = next_cursor
      projections: list[_Projection] = []
      for block in blocks:
        block_id = typing.cast(int, block.id)
        processed += 1
        try:
          projections.append(await cls._project(block))
        except _ProjectionUnavailable as error:
          report.record(block_id, "unavailable", str(error))
        except Exception as error:
          logger.exception("Lexical projection failed", extra={"block": block_id})
          report.record(block_id, "failed", type(error).__name__)
      if projections:
        try:
          async with lexical_uow() as repository:
            await repository.upsert(
              [
                {
                  "block": projection.block,
                  "label": projection.label,
                  "text": projection.text,
                }
                for projection in projections
              ]
            )
        except Exception as error:
          logger.exception(
            "Lexical projection batch upsert failed",
            extra={"batch_size": len(projections)},
          )
          for projection in projections:
            report.record(projection.block, "failed", type(error).__name__)
        else:
          report.indexed += len(projections)
      if exhausted:
        break
    return report.build()

  @classmethod
  async def _project(cls, block: BlockModel) -> _Projection:
    block_id = block.id
    if block_id is None:
      raise RuntimeError("Persisted lexical candidate is missing its ID")
    try:
      resolver = ResolverManager.get(block)
      label = await resolver.get_label()
      text = await resolver.get_text(context="lexical", materialize_missing=True)
    except UnknownResolverError as error:
      raise _ProjectionUnavailable("unknown_resolver") from error
    except UnsupportedResolverCapability as error:
      raise _ProjectionUnavailable("unsupported_lexical_text") from error
    normalized_label = label.strip()
    normalized_text = text.strip() if text is not None and text.strip() else None
    if not normalized_label:
      raise _ProjectionUnavailable("empty_label")
    return _Projection(block_id, normalized_label, normalized_text)

  @staticmethod
  def _excerpt(label: str, text: str | None, query: str) -> str:
    source = text or label
    folded_source = source.casefold()
    needle = query.casefold()
    position = folded_source.find(needle)
    if position < 0:
      positions = [
        candidate
        for term in query.split()
        if (candidate := folded_source.find(term.casefold())) >= 0
      ]
      position = min(positions, default=0)
    start = max(0, position - _EXCERPT_LIMIT // 3)
    end = min(len(source), start + _EXCERPT_LIMIT)
    prefix = "…" if start else ""
    suffix = "…" if end < len(source) else ""
    return f"{prefix}{source[start:end]}{suffix}"
