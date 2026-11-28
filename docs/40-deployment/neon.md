# Neon

## Why This Doc Exists

Neon is part of the repository's operational model, both for runtime compatibility and for branch-based CI workflows.

## Scale-To-Zero Compatibility

If the database can scale to zero when idle, set:

```bash
DATABASE_SCALE_0=true
```

Current checked-in behavior:

- `app/settings.py` exposes `database_scale_0`
- `app/engine.py` enables `pool_pre_ping` when it is true

This exists to reduce stale-connection failures when the database has gone idle.

## Branching Workflow

Neon branch automation is defined in `.github/workflows/branching-database.yml`.

Trusted pull requests use one data-free branch named `preview/core-py/pr-<number>`:

1. branch the durable `preview-base` from canonical `production`, then run the guarded
   sanitizer once to remove every allowlisted application row while preserving
   `alembic_version`
2. create or reuse the deterministic branch with a seven-day TTL
3. 交付 controller 校验分支身份并处理首次引导所需的继承角色清理。精确 PR 镜像执行迁移、
   catalog reconciliation 和 readiness
4. delete the same deterministic branch when the PR closes

The workflow pins Neon CLI 2.36.0. Connection URLs are consumed only through masked action
outputs and are never written to logs or artifacts.

`preview-base` is a normal Neon branch because schema-only branching is incompatible with
the legacy `authenticated` web role on the current runtime branch. Its one-time bootstrap
copies the source branch internally, immediately truncates every known application table,
resets the cloned database identity from `production` to `runtime`, and verifies the database
remains at the repository head. The identity reset is limited to this guarded, data-free
baseline; canonical production remains immutable.
Production is not mutated, and production rows never enter a PR-owned branch.

The current Neon free-v3 plan has a protected-branch quota of zero, so provider protection
cannot be enabled on `preview-base`. The sanitizer requires the exact branch name, and the
PR cleanup workflow only targets the exact repository-qualified PR identity. Upgrade the Neon plan
or revisit branch protection before broadening administrative access.

The same plan currently permits ten branches. Keep disposable test branches short-lived
and delete them as soon as no active session depends on them. Ordinary push validation does
not allocate a database branch.

Application bootstrap may create its required runtime records after preview deployment, but
production data is never treated as seed data. Canonical `production` is the required parent
whenever `preview-base` is replaced. Sanitization must finish and prove zero application rows
before any PR child is created.

## Manual Recovery Evidence

Production schema maintenance uses a native Neon branch as its recovery point. After both
application services are stopped and the database inventory has no active Peer lease, running
Job, or database session other than idle client backends without open transactions,
the protected workflow reads `pg_current_wal_flush_lsn()` in a read-only transaction. It creates
a child of the exact configured production branch at that LSN, with no compute endpoint and no
expiration, and verifies the parent, LSN, branch identity, default mapping, and absence of an
endpoint before allowing migration. The branch quota check reserves one slot for this backup
and one more for a preserved recovery branch. If the provider reports less capacity, maintenance
stops before migration. Neon documents [`parent_id` and `parent_lsn` for branch creation](https://api-docs.neon.tech/reference/createprojectbranch)
and requires `preserve_under_name` when [restoring a branch that has children](https://api-docs.neon.tech/reference/restoreprojectbranch).

The recovery branch captures database data and SQL-level roles, memberships, and grants at the
chosen LSN. Neon role passwords are control-plane state: restoring branch data does not restore
an earlier password. Keep the current configured production credentials authoritative and
verify them against the restored branch before starting either service. Do not place credentials
in a branch receipt or database manifest.

If migration has changed production and recovery is required, stop both services and restore the
existing production branch from the confirmed backup branch at its current head. Production
already has child branches, including retained previews and backups, so Neon requires
`preserve_under_name`; this saves the failed current state under a new branch and moves its
children there. Verify the production branch ID, default status, endpoint IDs, parent relationship,
and preserved child list after restore. Do not start an old image until the schema has been
verified compatible with it. Keep both the recovery branch and preserved failed-state branch
until the recovery window is closed.

The restore request targets the existing production branch ID and names the backup branch as its
source. Omit `source_lsn` to restore the backup branch's current head, and provide a unique
`preserve_under_name`, for example:

```json
{
  "source_branch_id": "br-maintenance-backup",
  "preserve_under_name": "core-py-failed-state-<run-id>"
}
```

The isolated recovery qualification exercised a branch created at a fixed parent LSN, then restored
its parent while preserving a child-bearing target. The restored target returned to the backup data
point, while the preserved branch retained the later data point. Role names, membership, table
ACLs, and SELECT behavior matched across source, restored target, and preserved state. The separate
3d-to-e6 migration qualification preserved all 23 pre-existing application-table hashes, the
legacy selection snapshot, and the role/membership manifest. These checks do not claim password
rollback; credentials remain a separate control-plane invariant.

For other operator-led backup/restore or branch cutovers that need portable archive evidence：

1. create the intended backup or Neon checkpoint from the live branch;
2. stream a PostgreSQL custom archive directly into encryption without writing plaintext;
3. retain object privileges in the archive, but exclude provider-owned default ACL entries
   from the restore list;
4. restore into a separate branch;
5. compare a value-free manifest containing the Alembic head and every application-table
   row count;
6. require an empty provider schema diff and a passing checked-in readiness command.

For an isolated Neon snapshot restore qualification, keep the restore unfinalized and inspect the new
branch returned by the restore operation. Record the production branch and endpoint identities
before the request, then verify the restored branch and its compute belong to the new branch and
the production mappings remain unchanged. Do not target or finalize the production branch during
a qualification. Wait for every returned operation to finish before connecting.

Run candidate schema initialization only against the verified restore branch. Compare the same
snapshot before and after migration using hashes or value-free manifests: all unchanged application
tables, shared Extension config/state, migrated legacy selection data and the required database
roles must remain intact. Keep raw data and credentials out of CI artifacts and summaries; retain
only the resource identities and non-secret verification results in the existing recovery record.
Snapshot and restore-branch cleanup is an explicit operator action after the recovery window closes.

Do not drop and recreate Neon's `public` schema during restore. A database-owner connection
cannot recreate provider-owned default privileges, while `pg_restore --clean` can replace the
archive-listed application objects without disturbing those provider defaults.

The value-free manifest command is:

```bash
DATABASE_URL=... pdm run db:manifest
```

The manifest records the database's observed lineage，application schema and row counts。It is an operator-invoked
backup/restore observation，not an automatic production-deployment gate；`db ready` owns the strict current artifact contract。

Portable archives, checksums, manifests, and credentials are operational artifacts outside
Git and CI. Production rows are recovery data, never seed data.

## Legacy Schema Convergence

An Alembic head is lineage evidence, not proof that a database matches current metadata.
When a published history has been rewritten or a long-lived database predates it:

1. preserve the live dataset and collect `alembic check` evidence on a disposable copy;
2. correct metadata to the intended contract instead of blindly accepting generated drift;
3. append one convergence revision that works from both the fresh and legacy shapes;
4. refuse lossy narrowing and unexpected NULL-value repair;
5. compare value-free manifests before and after the migration;
6. require `alembic check`, readiness, and the fresh-database artifact job to pass.

Do not stamp a legacy database past a convergence revision. Stamping changes lineage only;
it neither applies nor verifies the schema transition.

## Production Branch

The canonical production branch is named `production`. Since the 2026-08-13 project-root
migration it is the no-TTL root and default branch of the active Neon project. It was restored
from a directly encrypted custom archive whose value-free source and target manifests and
normalized schema dumps matched exactly. GitHub stores the expected parent identity as the
literal value `null` so the delivery guard can distinguish this topology from a missing value.

Native runtime and PostgREST use role-specific URLs derived from the pooled branch
coordinate. The protected GitHub lifecycle process alone receives the direct owner
`MIGRATION_DATABASE_URL`; Heroku config never contains that URL. Both coordinates are
resolved from the same exact guarded branch during each delivery.

Ordinary production delivery resolves both owner coordinates directly from the configured branch ID，then lets database
convergence and readiness expose unavailable or incompatible state。Operator-led backups and restore drills remain separate
operations rather than mandatory proof gates on every deployment。

## Current Topology And Retired Project

There is no active Neon staging or develop environment. The active topology is intentionally
small:

- root/default `production` is the sole canonical runtime branch;
- `preview-base` is the sanitized no-TTL child of production;
- only open, trusted pull requests own seven-day `preview/core-py/pr-<number>` children.

The former project, including its provider-required `master` root and historical staging
lineage, was soft-deleted after production and all open-PR consumers were proven on the new
project. Its GitHub API keys were revoked. Neon retains the deleted project for its provider
recovery window only; no runtime, workflow, or credential may address it.

## Operational Implication

Neon branch lifecycle and scale-to-zero behavior are deployment truths. They should stay documented here rather than mixed into product docs or task notes.
