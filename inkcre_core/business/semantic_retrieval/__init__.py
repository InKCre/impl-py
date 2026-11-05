"""Profile-scoped local semantic retrieval and embedding maintenance."""

from .main import (
  EmbeddingProfileNotFoundError,
  InvalidSemanticVectorError,
  SEMANTIC_RETRIEVAL_CONFIG_KEY,
  SEMANTIC_RETRIEVAL_CONFIG_SCHEMA,
  SEMANTIC_RETRIEVAL_CAPABILITY,
  SemanticRetrievalDelegationError,
  SemanticRetrievalError,
  SemanticRetrievalManager,
  SemanticRetrievalNotConfiguredError,
)
from .job import (
  SEMANTIC_MAINTAIN_JOB_TYPE,
  SEMANTIC_REBUILD_JOB_TYPE,
  SemanticMaintainJobHandler,
  SemanticRebuildJobHandler,
)

__all__ = [
  "EmbeddingProfileNotFoundError",
  "InvalidSemanticVectorError",
  "SEMANTIC_RETRIEVAL_CONFIG_KEY",
  "SEMANTIC_RETRIEVAL_CONFIG_SCHEMA",
  "SEMANTIC_RETRIEVAL_CAPABILITY",
  "SemanticRetrievalDelegationError",
  "SemanticRetrievalError",
  "SemanticRetrievalManager",
  "SemanticRetrievalNotConfiguredError",
  "SEMANTIC_MAINTAIN_JOB_TYPE",
  "SEMANTIC_REBUILD_JOB_TYPE",
  "SemanticMaintainJobHandler",
  "SemanticRebuildJobHandler",
]
