"""Add native collection identities and atomic Source graph batches.

Revision ID: f1b2c3d4e5a6
Revises: dcf44bc9af73
"""

from alembic import op
import sqlalchemy as sa


revision = "f1b2c3d4e5a6"
down_revision = "dcf44bc9af73"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.create_table(
    "collection_records",
    sa.Column("namespace", sa.Text(), nullable=False),
    sa.Column("native_key", sa.Text(), nullable=False),
    sa.Column("block", sa.Integer(), nullable=False),
    sa.CheckConstraint(
      "length(namespace) BETWEEN 1 AND 200", name="collection_record_namespace"
    ),
    sa.CheckConstraint(
      "length(native_key) BETWEEN 1 AND 1000", name="collection_record_key"
    ),
    sa.ForeignKeyConstraint(
      ["block"], ["inkcre.blocks.id"], ondelete="CASCADE", onupdate="CASCADE"
    ),
    sa.PrimaryKeyConstraint("namespace", "native_key"),
    schema="inkcre",
  )
  op.create_index(
    "ix_inkcre_collection_records_block", "collection_records", ["block"], schema="inkcre"
  )
  op.execute("""
    CREATE FUNCTION inkcre.commit_source_batch(
      source_id integer,
      namespace text,
      expected_config jsonb,
      expected_state jsonb,
      next_state jsonb,
      records jsonb,
      relations jsonb
    ) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER
    SET search_path = pg_catalog
    AS $function$
    DECLARE
      source_config jsonb;
      source_state jsonb;
      item jsonb;
      block_row record;
      block_id integer;
      from_id integer;
      to_id integer;
      result_blocks jsonb := '{}'::jsonb;
    BEGIN
      IF namespace IS NULL OR length(namespace) NOT BETWEEN 1 AND 200
        OR namespace !~ '[^[:space:]]'
        OR jsonb_typeof(expected_config) IS DISTINCT FROM 'object'
        OR jsonb_typeof(expected_state) IS DISTINCT FROM 'object'
        OR jsonb_typeof(next_state) IS DISTINCT FROM 'object'
        OR jsonb_typeof(records) IS DISTINCT FROM 'array'
        OR jsonb_typeof(relations) IS DISTINCT FROM 'array'
      THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid Source batch shape';
      END IF;
      IF jsonb_array_length(records) > 500 OR jsonb_array_length(relations) > 2000
        OR octet_length((expected_config::text || expected_state::text || next_state::text
          || records::text || relations::text)) > 4194304
      THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Source batch exceeds size limits';
      END IF;
      IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(records) AS entry
        GROUP BY entry ->> 'key' HAVING count(*) > 1
      ) THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'duplicate native key in Source batch';
      END IF;

      SELECT source.config, source.state INTO source_config, source_state
      FROM inkcre.sources AS source WHERE source.id = source_id FOR UPDATE;
      IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'Source does not exist';
      END IF;
      IF coalesce(source_config, '{}'::jsonb) IS DISTINCT FROM expected_config
        OR coalesce(source_state, '{}'::jsonb) IS DISTINCT FROM expected_state
      THEN
        RAISE EXCEPTION USING ERRCODE = 'PT409', MESSAGE = 'Source configuration or state changed';
      END IF;

      -- Serialize identities across Sources, not just executions of this Source.
      PERFORM pg_advisory_xact_lock(hashtextextended('collection-records:' || namespace, 0));
      FOR item IN SELECT value FROM jsonb_array_elements(records)
      LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'object'
          OR item - ARRAY['key', 'resolver', 'content'] <> '{}'::jsonb
          OR jsonb_typeof(item -> 'key') IS DISTINCT FROM 'string'
          OR length(item ->> 'key') NOT BETWEEN 1 AND 1000
          OR (item ->> 'key') !~ '[^[:space:]]'
          OR jsonb_typeof(item -> 'resolver') IS DISTINCT FROM 'string'
          OR length(item ->> 'resolver') NOT BETWEEN 1 AND 500
          OR (item ->> 'resolver') !~ '[^[:space:]]'
          OR jsonb_typeof(item -> 'content') IS DISTINCT FROM 'object'
        THEN
          RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid Source record';
        END IF;
        SELECT block.* INTO block_row
        FROM inkcre.collection_records AS identity
        JOIN inkcre.blocks AS block ON block.id = identity.block
        WHERE identity.namespace = $2 AND identity.native_key = item ->> 'key'
        FOR UPDATE OF block;
        IF FOUND THEN
          IF block_row.resolver IS DISTINCT FROM item ->> 'resolver'
            OR block_row.storage IS NOT NULL
            OR jsonb_typeof(block_row.content::jsonb) IS DISTINCT FROM 'object'
          THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'native identity is bound to an incompatible Block';
          END IF;
          block_id := block_row.id;
          UPDATE inkcre.blocks AS block
          SET content = (block_row.content::jsonb || (item -> 'content'))::text
          WHERE block.id = block_id;
        ELSE
          INSERT INTO inkcre.blocks (storage, resolver, content)
          VALUES (NULL, item ->> 'resolver', (item -> 'content')::text)
          RETURNING id INTO block_id;
          INSERT INTO inkcre.collection_records (namespace, native_key, block)
          VALUES ($2, item ->> 'key', block_id);
        END IF;
        result_blocks := result_blocks || jsonb_build_object(item ->> 'key', block_id);
      END LOOP;

      FOR item IN SELECT value FROM jsonb_array_elements(relations)
      LOOP
        IF jsonb_typeof(item) IS DISTINCT FROM 'object'
          OR item - ARRAY['from_key', 'to_key', 'content'] <> '{}'::jsonb
          OR jsonb_typeof(item -> 'from_key') IS DISTINCT FROM 'string'
          OR jsonb_typeof(item -> 'to_key') IS DISTINCT FROM 'string'
          OR jsonb_typeof(item -> 'content') IS DISTINCT FROM 'string'
          OR length(item ->> 'content') NOT BETWEEN 1 AND 1000
        THEN
          RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'invalid Source relation';
        END IF;
        SELECT identity.block INTO from_id FROM inkcre.collection_records AS identity
        WHERE identity.namespace = $2 AND identity.native_key = item ->> 'from_key';
        SELECT identity.block INTO to_id FROM inkcre.collection_records AS identity
        WHERE identity.namespace = $2 AND identity.native_key = item ->> 'to_key';
        IF from_id IS NULL OR to_id IS NULL THEN
          RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Source relation endpoint is unknown';
        END IF;
        INSERT INTO inkcre.relations (from_, to_, content)
        SELECT from_id, to_id, item ->> 'content'
        WHERE NOT EXISTS (
          SELECT 1 FROM inkcre.relations AS relation
          WHERE relation.from_ = from_id AND relation.to_ = to_id
            AND relation.content = item ->> 'content'
        );
      END LOOP;
      UPDATE inkcre.sources AS source SET state = next_state WHERE source.id = source_id;
      RETURN jsonb_build_object('blocks', result_blocks, 'state', next_state);
    END;
    $function$;
  """)
  op.execute(
    "REVOKE ALL ON FUNCTION inkcre.commit_source_batch(integer, text, jsonb, jsonb, jsonb, jsonb, jsonb) FROM PUBLIC"
  )
  op.execute(
    "UPDATE inkcre_internal.contract_state "
    "SET contract_revision='source-batch-persistence-v1',updated_at=CURRENT_TIMESTAMP "
    "WHERE singleton"
  )


def downgrade() -> None:
  op.execute(
    "DROP FUNCTION inkcre.commit_source_batch(integer, text, jsonb, jsonb, jsonb, jsonb, jsonb)"
  )
  op.drop_table("collection_records", schema="inkcre")
  op.execute(
    "UPDATE inkcre_internal.contract_state "
    "SET contract_revision='extension-distribution-open-v1',updated_at=CURRENT_TIMESTAMP "
    "WHERE singleton"
  )
