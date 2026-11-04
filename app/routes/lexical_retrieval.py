"""Fixed lexical-retrieval HTTP inbound."""

__all__ = ["PEER_INBOUND", "ROUTER"]

import fastapi

from inkcre_core.business.lexical_retrieval import (
  LEXICAL_RETRIEVAL_CAPABILITY,
  LexicalRetrievalManager,
)
from inkcre_core.business.peer import PeerHTTPInbound
from inkcre_core.schemas.lexical_retrieval import (
  LexicalRetrievalRequest,
  LexicalRetrievalResult,
)


ROUTER = fastapi.APIRouter(tags=["lexical-retrieval"])
PEER_INBOUND = PeerHTTPInbound(
  capability=LEXICAL_RETRIEVAL_CAPABILITY,
  method="POST",
  path="/lexical-retrieval",
)


@ROUTER.post("/lexical-retrieval")
async def retrieve(body: LexicalRetrievalRequest) -> LexicalRetrievalResult:
  return await LexicalRetrievalManager.retrieve_local(body.query, body.limit)
