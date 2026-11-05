# InKCre Python Implementation

The InKCre implemented in Python. Owns the authoritative database schema and migrations, hosts `inkcre-cli`, and ships Python-implemented first-party extensions.

## Mental Model

InKCre is a system of heterogeneous peers sharing one info-base. Peers differ in runtime capability, not in authority: no fixed frontend/backend hierarchy exists.

A single admitted database protocol carries both deployment state and persisted information. Every authenticated peer can operate the full protocol surface, submit jobs, and claim work it can execute. `impl-py` owns the authoritative database schema and migrations; that does not make it a controller of other peers.

Collection, organization, and application are three distinct activities around shared information. Information and business state do not belong to any executing node and do not disappear when a node stops.

Extensions add capabilities to the peer they run on. Different Distributions of the same extension participate in the shared system under one business and data contract; no Distribution is the "central" implementation.

Before choosing a design, identify shared state, business contracts, and each participant's capability. Then pick direct read/write, job execution, or peer delegation. The authoritative model is in [unit topology](docs/_shared/20-product-tdd/unit-topology.md), [system state and authority](docs/_shared/20-product-tdd/system-state-and-authority.md), and the [knowledge capability contract](docs/_shared/20-product-tdd/knowledge-capability-contract.md).

## Knowledge Owners

`impl-py` is an SVC Spoke. Shared Hub truth is mounted read-only at `docs/_shared/` (git submodule → `InKCre/docs`).

- Product behavior and language: `docs/_shared/10-prd/`
- Cross-unit contracts and topology: `docs/_shared/20-product-tdd/`
- `impl-py` internal design: `docs/30-unit-tdd/`
- Runtime, packaging, migration, observability, and recovery: `docs/40-deployment/`
- Shared security model: `docs/_shared/20-product-tdd/security-boundary-model.md`; vulnerability reporting: `SECURITY.md`; runtime realization: `docs/30-unit-tdd/security-model.md`
- Volatile task state: `tasks/`
- Mechanically enforceable facts: code, configuration, schemas, tests, and CI
- Subtree-specific hazards: the nearest local `AGENTS.md`

## Repository Map

- `run.py`: FastAPI bootstrap
- `inkcre_core/`: independently consumable SDK, models, domain operations, persistence and execution
- `app/`: service routes, configuration, extension/sink composition and legacy import shims
- `cli/`: `inkcre-cli` implementation
- `extensions/`: built-in first-party extension packages
- `libs/`, `utils/`: legacy imports; implementations live under `inkcre_core/`
- `migrations/`: Alembic revisions and integrity baseline (append-only after deployment)
- `tests/`: migration integrity and mature integration/acceptance checks
- `docs/30-unit-tdd/`: `impl-py` internal design
- `docs/40-deployment/`: runtime, packaging, migration, observability, recovery

## Development Constraints

- Package management: PDM. Run Python through `pdm run`.
- `.env` files are dotenv data, not shell scripts. Load them through the application because values may contain shell metacharacters.
- Tests that create or destroy database roles, schemas, or databases require a disposable PostgreSQL runtime; never point them at a shared development or preview database.
- Prefer preview for end-to-end acceptance that depends on real third-party credentials, external protocol behavior, or production-shaped persistence/routing; use local development runtimes for fast deterministic iteration.
- Extract logic after the third occurrence. Export frequently used package items from `__init__.py`.

## Governance And Workflow

- Git and GitHub workflow: [Git and GitHub Governance](https://github.com/InKCre/.github/blob/main/GOVERNANCE.md) and [Contributing](https://github.com/InKCre/.github/blob/main/CONTRIBUTING.md)
- Verification and test selection: [Verification and Test Policy](https://github.com/InKCre/.github/blob/main/TESTING.md)
- Hub-Spoke shared-doc workflow: `.agents/skills/edit-svc-shared-docs/`

<!-- svc:begin -->
## SVC

Use `svc --help` or `svc <command> --help`.

- `svc status`: inspect project state
- `svc dev`: manage declared development targets

If `AGENTS.local.md` exists, read it after this file. It is ignored local guidance; shared rules belong here.
<!-- svc:end -->
