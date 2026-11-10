"""add block discovery index

Revision ID: 0ab4b22c6d3b
Revises: dcf44bc9af73
Create Date: 2026-11-11 00:22:29.889891

"""

from typing import Sequence

from alembic import op

from inkcre_core.database_contract import PROTOCOL_SCHEMA


# revision identifiers, used by Alembic.
revision: str = "0ab4b22c6d3b"
down_revision: str | Sequence[str] | None = "dcf44bc9af73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  # Ordinary transactional index build: blocks writes wait until completion.
  op.create_index(
    "blocks_resolver_id_idx",
    "blocks",
    ["resolver", "id"],
    unique=False,
    schema=PROTOCOL_SCHEMA,
  )


def downgrade() -> None:
  op.drop_index(
    "blocks_resolver_id_idx",
    table_name="blocks",
    schema=PROTOCOL_SCHEMA,
  )
