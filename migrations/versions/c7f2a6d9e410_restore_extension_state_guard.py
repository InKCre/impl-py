"""Restore the Extension state trigger after the Peer-participation cutover.

Revision ID: c7f2a6d9e410
Revises: bb5dcb562587
"""

from alembic import op

from inkcre_core.database_contract import INTERNAL_SCHEMA, PROTOCOL_SCHEMA


revision = "c7f2a6d9e410"
down_revision = "bb5dcb562587"
branch_labels = None
depends_on = None


def upgrade() -> None:
  # e6a1b2c3d4f5 replaced the guard function but did not reattach its trigger.
  # Existing rows stay untouched; future writes obey the original state contract.
  op.execute(
    f"""
    CREATE TRIGGER extensions_state_authority
    BEFORE INSERT OR UPDATE OR DELETE ON {PROTOCOL_SCHEMA}.extensions
    FOR EACH ROW
    EXECUTE FUNCTION {INTERNAL_SCHEMA}.enforce_extension_state_authority()
    """
  )


def downgrade() -> None:
  op.execute(f"DROP TRIGGER extensions_state_authority ON {PROTOCOL_SCHEMA}.extensions")
