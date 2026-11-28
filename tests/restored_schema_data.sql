-- The portable schema carries lifecycle metadata only. Inspect the restored
-- database before init can reconcile the revision or install builtins.
BEGIN READ ONLY;
SET LOCAL inkcre_test.expected_revision = :'expected_revision';
DO $$
DECLARE
  relation regclass;
  has_rows boolean;
BEGIN
  IF NOT EXISTS (
    SELECT FROM inkcre_internal.contract_state
    WHERE singleton AND environment = 'runtime'
      AND contract_revision = current_setting('inkcre_test.expected_revision')
  ) THEN
    RAISE EXCEPTION 'restored contract state must carry the neutral runtime identity and exported revision';
  END IF;
  FOR relation IN
    SELECT oid::regclass FROM pg_class
    WHERE relnamespace = 'inkcre'::regnamespace AND relkind IN ('r', 'p')
  LOOP
    EXECUTE format('SELECT EXISTS (SELECT FROM %s)', relation) INTO has_rows;
    IF has_rows THEN
      RAISE EXCEPTION 'portable schema contains application rows in %', relation;
    END IF;
  END LOOP;
END $$;
COMMIT;
