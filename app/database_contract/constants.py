"""Stable names and values shared by migrations, runtimes, and consumers."""

import uuid


CONTRACT_FORMAT = 1
CONTRACT_REVISION = "extension-distribution-open-v1"

PROTOCOL_SCHEMA = "inkcre"
INTERNAL_SCHEMA = "inkcre_internal"

AUTHENTICATED_ROLE = "authenticated"
AUTHENTICATOR_ROLE = "authenticator"
ANONYMOUS_ROLE = "anonymous"
CORE_RUNTIME_ROLE = "inkcre_core"

JWT_ALGORITHM = "HS256"
JWT_ROLE = AUTHENTICATED_ROLE
JWT_ISSUER = "inkcre-peer"
JWT_AUDIENCE = "inkcre-api"
# Frozen input for admitted historical migrations; live admission has no lifetime cap.
JWT_MAX_LIFETIME_SECONDS = 24 * 60 * 60
JWT_MINIMUM_SECRET_BYTES = 32

DEVELOPMENT_PEER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DEVELOPMENT_PEER_NAME = "client-web-development"

DATABASE_ENVIRONMENTS = frozenset({"runtime", "development", "preview", "production"})
RESET_CONFIRMATION = "reset-development-data"

APPLICATION_TABLES = (
  "agents",
  "ai_dialects",
  "ai_models",
  "ai_providers",
  "block_embeddings",
  "block_lexical_records",
  "blocks",
  "peers",
  "configs",
  "extensions",
  "extension_peer_participations",
  "embedding_profiles",
  "crons",
  "jobs",
  "job_types",
  "logs",
  "relation_embeddings",
  "relations",
  "sink_types",
  "sinks",
  "sources",
  "sources_types",
  "storage_types",
  "storage_blobs",
  "storages",
)
