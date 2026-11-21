"""Add the optional publisher repository to each extension selection.

Revision ID: 6fa473d9fc44
Revises: bb5dcb562587
Create Date: 2026-11-21 01:50:12.265467

"""

from typing import Sequence

from alembic import op
from inkcre_core.database_contract import PROTOCOL_SCHEMA
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6fa473d9fc44"
down_revision: str | Sequence[str] | None = "bb5dcb562587"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.add_column(
    "extension_peer_participations",
    sa.Column("source_repository", sa.Text(), nullable=True),
    schema=PROTOCOL_SCHEMA,
  )
  op.execute("NOTIFY pgrst, 'reload schema'")


def downgrade() -> None:
  op.drop_column(
    "extension_peer_participations", "source_repository", schema=PROTOCOL_SCHEMA
  )
  op.execute("NOTIFY pgrst, 'reload schema'")
