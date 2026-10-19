"""Make JWT signing defaults independent of admission.

Revision ID: 09a921a95314
Revises: e6a1b2c3d4f5
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "09a921a95314"
down_revision: str | Sequence[str] | None = "e6a1b2c3d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_check_jwt(*, enforce_legacy_maximum: bool) -> None:
  legacy_maximum = "OR expires_at - issued_at > 86400" if enforce_legacy_maximum else ""
  op.execute(
    f"""
    CREATE OR REPLACE FUNCTION inkcre_internal.check_jwt()
    RETURNS void
    LANGUAGE plpgsql
    SECURITY INVOKER
    SET search_path = pg_catalog
    AS $$
    DECLARE
      claims jsonb;
      issued_at numeric;
      expires_at numeric;
      current_epoch numeric := extract(epoch FROM statement_timestamp());
    BEGIN
      claims := nullif(current_setting('request.jwt.claims', true), '')::jsonb;
      IF claims IS NULL
         OR jsonb_typeof(claims -> 'role') IS DISTINCT FROM 'string'
         OR claims ->> 'role' IS DISTINCT FROM 'authenticated'
         OR jsonb_typeof(claims -> 'iss') IS DISTINCT FROM 'string'
         OR claims ->> 'iss' IS DISTINCT FROM 'inkcre-peer'
         OR jsonb_typeof(claims -> 'aud') IS DISTINCT FROM 'string'
         OR claims ->> 'aud' IS DISTINCT FROM 'inkcre-api'
         OR jsonb_typeof(claims -> 'iat') IS DISTINCT FROM 'number'
         OR jsonb_typeof(claims -> 'exp') IS DISTINCT FROM 'number'
      THEN
        RAISE insufficient_privilege USING MESSAGE = 'invalid jwt claims';
      END IF;

      issued_at := (claims ->> 'iat')::numeric;
      expires_at := (claims ->> 'exp')::numeric;
      IF issued_at > current_epoch + 60
         OR expires_at <= current_epoch
         OR expires_at <= issued_at
         {legacy_maximum}
      THEN
        RAISE insufficient_privilege USING MESSAGE = 'invalid jwt lifetime';
      END IF;
    END
    $$
    """
  )


def _set_contract_revision(value: str) -> None:
  op.execute(
    sa.text(
      """
      UPDATE inkcre_internal.contract_state
      SET contract_revision = :revision, updated_at = CURRENT_TIMESTAMP
      WHERE singleton
      """
    ).bindparams(revision=value)
  )
  op.execute("NOTIFY pgrst, 'reload schema'")


def upgrade() -> None:
  """Keep claim and time validation without a maximum token lifetime."""
  _replace_check_jwt(enforce_legacy_maximum=False)
  _set_contract_revision("peer-jwt-signing-defaults-v1")


def downgrade() -> None:
  """Restore the previous admission limit; existing longer tokens are rejected."""
  _replace_check_jwt(enforce_legacy_maximum=True)
  _set_contract_revision("extension-peer-participation-v2")
