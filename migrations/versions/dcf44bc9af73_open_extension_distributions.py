"""Allow Peer-owned distribution formats.

Revision ID: dcf44bc9af73
Revises: 09a921a95314
"""

from alembic import op

revision = 'dcf44bc9af73'
down_revision = '09a921a95314'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('extension_peer_participations_distribution_valid',
                       'extension_peer_participations', schema='inkcre', type_='check')
    op.execute("UPDATE inkcre_internal.contract_state SET contract_revision='extension-distribution-open-v1',updated_at=CURRENT_TIMESTAMP WHERE singleton")


def downgrade() -> None:
    op.create_check_constraint('extension_peer_participations_distribution_valid',
                               'extension_peer_participations',
                               "distribution IS NULL OR distribution IN ('python','module_federation')", schema='inkcre')
    op.execute("UPDATE inkcre_internal.contract_state SET contract_revision='peer-jwt-signing-defaults-v1',updated_at=CURRENT_TIMESTAMP WHERE singleton")
