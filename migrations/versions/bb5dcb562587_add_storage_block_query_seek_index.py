"""add storage Block query seek index

Revision ID: bb5dcb562587
Revises: 0ab4b22c6d3b
Create Date: 2026-11-11 19:53:01.662284

"""

from typing import Sequence

from alembic import op
from inkcre_core.database_contract import PROTOCOL_SCHEMA


# revision identifiers, used by Alembic.
revision: str = "bb5dcb562587"
down_revision: str | Sequence[str] | None = "0ab4b22c6d3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  # Transactional build can block writes; schedule a maintenance window at scale.
  op.create_index(
    "blocks_storage_id_idx",
    "blocks",
    ["storage", "id"],
    unique=False,
    schema=PROTOCOL_SCHEMA,
  )


def downgrade() -> None:
  op.drop_index("blocks_storage_id_idx", table_name="blocks", schema=PROTOCOL_SCHEMA)
