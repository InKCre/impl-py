"""Preserve Source anchor identity while linking collected graph roots.

Revision ID: f1b2c3d4e5a8
Revises: f1b2c3d4e5a7
"""

from alembic import op


revision = "f1b2c3d4e5a8"
down_revision = "f1b2c3d4e5a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.execute("""
    CREATE OR REPLACE FUNCTION inkcre.commit_source_batch(
      source_id integer,
      namespace text,
      expected_config jsonb,
      expected_state jsonb,
      next_state jsonb,
      records jsonb,
      relations jsonb,
      root_key text DEFAULT NULL
    ) RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY INVOKER
    SET search_path = pg_catalog
    AS $function$
    DECLARE
      source_config jsonb;
      source_state jsonb;
      source_type text;
      source_nickname text;
      source_anchor_id integer;
      anchor_content jsonb;
      anchor_row record;
      item jsonb;
      block_row record;
      block_id integer;
      from_id integer;
      to_id integer;
      root_block_id integer;
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

      SELECT source.config, source.state, source.type, source.nickname, source.block
      INTO source_config, source_state, source_type, source_nickname, source_anchor_id
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
      IF root_key IS NOT NULL THEN
        SELECT identity.block INTO root_block_id
        FROM inkcre.collection_records AS identity
        WHERE identity.namespace = $2 AND identity.native_key = root_key;
        IF root_block_id IS NULL THEN
          RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Source root identity is unknown';
        END IF;
        anchor_content := jsonb_build_object('id', source_id, 'type', source_type, 'nickname', source_nickname);
        IF source_anchor_id IS NULL THEN
          INSERT INTO inkcre.blocks (storage, resolver, content)
          VALUES (NULL, 'core.source.v1', anchor_content::text)
          RETURNING id INTO source_anchor_id;
        ELSE
          SELECT block.* INTO anchor_row
          FROM inkcre.blocks AS block WHERE block.id = source_anchor_id FOR UPDATE;
          IF NOT FOUND OR anchor_row.resolver IS DISTINCT FROM 'core.source.v1'
            OR anchor_row.storage IS NOT NULL
            OR jsonb_typeof(anchor_row.content::jsonb) IS DISTINCT FROM 'object'
            OR (anchor_row.content::jsonb -> 'id') IS DISTINCT FROM to_jsonb(source_id)
          THEN
            RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Source anchor is incompatible';
          END IF;
          UPDATE inkcre.blocks AS block SET content = anchor_content::text
          WHERE block.id = source_anchor_id;
        END IF;
        INSERT INTO inkcre.relations (from_, to_, content)
        SELECT source_anchor_id, root_block_id, 'collects'
        WHERE NOT EXISTS (
          SELECT 1 FROM inkcre.relations AS relation
          WHERE relation.from_ = source_anchor_id AND relation.to_ = root_block_id
            AND relation.content = 'collects'
        );
      END IF;
      UPDATE inkcre.sources AS source
      SET state = next_state, block = source_anchor_id
      WHERE source.id = source_id;
      RETURN jsonb_build_object('blocks', result_blocks, 'state', next_state, 'source_block', source_anchor_id);
    END;
    $function$;
  """)


def downgrade() -> None:
  op.execute("""
    CREATE OR REPLACE FUNCTION inkcre.commit_source_batch(
      source_id integer,
      namespace text,
      expected_config jsonb,
      expected_state jsonb,
      next_state jsonb,
      records jsonb,
      relations jsonb,
      root_key text DEFAULT NULL
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
      root_block_id integer;
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
      IF root_key IS NOT NULL THEN
        SELECT identity.block INTO root_block_id
        FROM inkcre.collection_records AS identity
        WHERE identity.namespace = $2 AND identity.native_key = root_key;
        IF root_block_id IS NULL THEN
          RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'Source root identity is unknown';
        END IF;
      END IF;
      UPDATE inkcre.sources AS source
      SET state = next_state, block = coalesce(root_block_id, source.block)
      WHERE source.id = source_id;
      RETURN jsonb_build_object('blocks', result_blocks, 'state', next_state);
    END;
    $function$;
  """)
