# Contributing

The organization-wide Git and pull-request policy lives in
[InKCre Git and GitHub Governance](https://github.com/InKCre/.github/blob/main/GOVERNANCE.md).
This guide adds the `core-py` setup, validation, migration, and delivery details required by that
policy; it does not weaken the organization baseline.

## Security Reports

Do not disclose suspected vulnerabilities in a public issue or pull request. Follow
[SECURITY.md](SECURITY.md) to report them privately. Use ordinary issues for functional
bugs that do not cross a security boundary.

## Development Setup

```bash
git submodule update --init --recursive
pdm install -G dev --frozen-lockfile
pdm run pre-commit install
pdm run doctor
pdm run check
```

`.python-version` is the shared Python runtime anchor. CI installs PDM 2.28.0, and
`doctor` reports a mismatch when the local toolchain does not match that contract.

Git hook 需要在每个本地 checkout 安装；提交前会执行与 CI 相同的格式和 lint 入口。
移动仓库目录后，需要重建含旧绝对路径的虚拟环境入口并重新安装 hook；CI 的完整
`check` 仍是交付检查依据。

`check` is the same hermetic repository gate used by CI. It verifies the lock and
requirements export, migration configuration and append-only baseline, Ruff formatting,
repository lint, and static types. Use narrower commands while iterating:

```bash
pdm run format:check
pdm run lint
pdm run check:foundation
```

Verification follows the organization-wide
[Verification and Test Policy](https://github.com/InKCre/.github/blob/main/TESTING.md). The remaining
migration, integration, and acceptance suites are repository-local admitted exceptions; they do not
authorize new unit, schema, helper, mocked-manager, or route tests by analogy.

## Release intent

Core, `cli/`, and each first-party Extension are independent release projects. A feature pull request that
changes delivered project behavior adds at least one non-empty project-local Towncrier fragment;
it does not change a version or generated changelog:

```bash
pdm run towncrier create --config towncrier.toml --dir extensions/<extension-id> +.<type>.md
pdm run check:releases --base origin/main
```

Core fragments live in `.changes/`; CLI fragments live in `cli/.changes/`; Extension fragments live in
`extensions/<extension-id>/.changes/`. Valid types are `added`, `changed`, `deprecated`, `removed`,
`fixed`, and `security`. Pure release-tooling or contributor-documentation changes do not invent
project news.

After feature fragments merge, the checked-main controller updates the independent `release/next`
pull request. Only that pull request runs `pdm run release:prepare`, consumes fragments, applies the
maturity-aware SemVer bump, and renders changelogs. It publishes nothing. Merging the prepared pull
request lets protected-main delivery publish changed Extensions; a prepared Core version change
selects normal Core production delivery. `workflow_dispatch` remains recovery.

## Database Migrations

Generate a candidate revision only after changing model metadata:

```bash
pdm run db:generate "describe the schema change"
```

Review and commit the generated file before applying it. Generation never upgrades a
database. After review, append its digest to the integrity baseline:

```bash
pdm run db:record
```

Apply checked-in revisions explicitly:

```bash
pdm run db:migrate
```

Deployment release processes may run `db:migrate`; they must never run `db:generate`.
Pull-request CI also rejects modifications, deletions, and renames of revisions that already
exist on the base branch; migration history is append-only.

## Container Contract

The OCI artifact has three provider-neutral commands:

```bash
python scripts/container.py web
python scripts/container.py migrate
python scripts/container.py ready
```

`web` honors `$PORT`; `migrate` only applies checked-in revisions; `ready` performs a
read-only connectivity and Alembic-head check. The Core image owns the supported dependency
baseline and excludes first-party Extension source. The Extension Host acquires only exact
published wheels and never resolves or mutates host dependencies while enabling them.

## Shared Docs And Skill Discovery

`core-py` is a Spoke repo and consumes shared Hub truth from `docs/_shared/`.

The canonical shared-doc editing skill lives in:

- `docs/_shared/00-meta/skills/edit-svc-shared-docs/`

Because Codex auto-loads repo-root `.agents/skills`, this repo also carries a thin discovery wrapper at:

- `.agents/skills/edit-svc-shared-docs/SKILL.md`

Use the repo-root wrapper only to discover the canonical skill. Do not fork the workflow into the wrapper.

## Shared-Doc Update Order

1. Capture the local pressure in the active Spoke task packet.
2. Edit shared docs in `InKCre/docs`.
3. Push the shared Hub commit first.
4. Bump `core-py/docs/_shared` to that pushed commit.
5. Keep Spoke-local runtime and implementation docs outside `docs/_shared`.
6. Do not mix shared-doc edits, ref bumps, and local implementation changes in one commit.
