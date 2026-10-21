# First-Party Extension Distribution

Core stores one shared Extension identity in `inkcre.extensions`: `name`, `nickname`, `config`,
and producer-owned `state`. Each Peer stores its exact native selection and enabled intent in
`inkcre.extension_peer_participations`, keyed by `(name, peer_id)`. A participation includes the
distribution, exact version, enabled intent, and the config schema published by that exact release.
Selection, enablement, and actual process runtime remain distinct states. No first-party rows are
seeded or synchronized.

For a new install or version change, Core requires an exact published Registry Release. Enabling
or cold-restoring an already-selected exact Release may consume a yanked Release, but missing or
blocked bytes leave that Peer’s enabled intent unchanged.

Python acquisition uses the Release's same-origin `/simple/<normalized-project>/` association.
Every enable or cold restore first reads the exact Registry Release, checks its lifecycle state and
Host SDK association, then discovers the exact record already installed in the Core interpreter.
Core reuses that Distribution only when its Project, entry point and Host SDK match the current
Registry association. A damaged or conflicting installed record fails; it is not silently replaced.
A missing record enters the independently released Runtime SDK acquisition path.

When the exact Distribution is absent, Core first downloads and validates its wheel without changing
the interpreter. It then closes Peer execution, drains jobs and scheduled callbacks, closes running
extensions, and applies the wheel with ordinary `sys.executable -m pip install` to the shared Core
environment. Pip may resolve dependencies from its configured package index and may change packages
used by other Extensions. After apply, Core replaces the process with a fresh one so Python imports
cannot continue using modules loaded before the environment change. An apply failure is reported and
leaves the Peer paused; Core does not claim package rollback. Exact installed-record reuse avoids
this lifecycle only when the installed project, entry point, and Host SDK match the Registry record.
The standard `inkcre.core.extensions` entry point is discovered globally and every loaded Extension
module is verified against the installed wheel file record. There is no per-Extension target
directory, custom ZIP loader or module search-path rewrite. Registry failure or a blocked Release
still prevents restoration even when its Distribution is installed; installed yanked Releases retain
their existing warning and admission behavior.

Peers may select different versions of the same shared Extension. Changing one Peer’s exact
selection is rejected while that Peer has the Extension enabled; other Peers’ selections do not
block it. A changed pin clears its config schema until the selected release starts and provides a
new schema. A process that already imported another version must restart; Core does not hot-load the
new class. `running` and `loaded_version` report observed runtime facts and are never inferred from
the durable enabled intent.

V2 management commands select or remove a Peer’s exact pin, enable or disable that Peer’s intent,
and report actual runtime status. A disabled pin may be removed; an enabled participation must first
be disabled. A legacy enabled member migrates to its exact prior version with an unresolved
distribution. Cold restore resolves only that exact Registry version and never substitutes the
latest release. Failed resolution preserves the old intent and reports the error.

The first-party Extensions are independent PEP 420 wheels with the standard entry-point group
and producer metadata in their `pyproject.toml`. That project version is the Python association's
Release authority and must equal the Extension Release Version shared by its Distribution
associations. Feature pull requests carry project-local Towncrier fragments and leave versions and
changelogs unchanged. After checked `main`, one generated Release PR consumes pending fragments,
applies each project's independent SemVer bump, and renders its changelog without publishing.
Repository orchestration owns project impact and next-version calculation；Towncrier owns fragment
validation and rendering。

The initial feature merge also wakes the publisher, but no project version has changed yet, so its
selection is a no-op. The Release PR merge runs repository checks against the generated release
state; only that checked `main` revision gives the existing publisher a new version to select and
publish. Leaving the Release PR open defers publication while later fragments update the same plan。

Repository CI discovers the producer set and rejects missing or invalid project intent。Publication
never runs from the Release PR。

The trusted pull-request preview controller reuses that same discovered producer set. Its dedicated
PDM `extension-preview` tooling group builds and verifies all discovered wheels, then the released
`inkcre-extension-toolkit` projects one explicit Python inventory into a static sibling Registry.
The exact-head workflow run deploys that tree directly to the dedicated Cloudflare Pages project;
required checks neither upload nor hand off wheels or deployable Registry output. The facade exposes
only exact Release reads, PEP 503 Simple HTML, wheels, and PEP 658 metadata. It adds no Registry
runtime, mutable lifecycle, token, or Core-hosted static route.

Preview delivery treats a successful Pages deployment command as sufficient delivery evidence. It
passes the deterministic PR alias to Core without downloading every published file, comparing
bytes, bypassing caches, or waiting for edge convergence. Consumer acceptance remains a separate
manual or black-box activity and does not block the Heroku preview from starting.

`.github/workflows/extension-publish.yml` prepares the exact Python Distribution Release at
`/v2/extensions/{namespace}/{name}/distributions/python/releases` with source provenance,
finalizes the newly built wheel using the pinned Toolkit's `python wheel finalize` command,
uploads through `/legacy/`, and publishes that Python Release. Automatic runs obtain `before_sha`
from the protected-main push and select only new projects or changed project versions;
unchanged matrix entries are explicit no-ops. The matrix and repository wheel checks consume the
same discovery result rather than separate project lists. An unprovable lineage or immutable
prepare conflict fails the job.

Preview and production both finalize before delivering the wheel. The installed runtime manifest belongs inside
`.dist-info/inkcre-extension.json`; Registry association metadata is not a replacement. The raw build output remains separate
from the finalized upload. Repairing a published wheel requires a new project version, not overwriting an old Release.

A checked commit may be older than current `main` only while it remains an ancestor. Before saving a
candidate, the capture job fetches `main` again and requires that Extension's artifact-input surface
to be unchanged across `HEAD..origin/main`. A generated changelog-only or different-Extension commit
therefore does not discard a valid build; a later change to the same artifact input stops the older
job before it records bytes for publication. Once capture succeeds, Registry retries use the saved
wheel, prepare metadata, source revision, and digest without consulting moving `main` again.

`workflow_dispatch` selects one exact discovered producer and is an `INITIAL_ONLY` lane for a
version that does not yet exist. Because public descriptors intentionally omit private producer
provenance, a new workflow run cannot safely resume an existing version: recovery must rerun the
original Extension publication run so its stable `github.run_id` build identity is preserved. The
capture job stores the complete candidate before the publisher receives Registry credentials.
Recovery reruns only the failed publish job, which locates and restores that run's unique artifact;
the artifact's source revision and wheel digest are checked before any Registry call. A full-workflow
rerun enters capture and fails explicitly at `Reject candidate reconstruction`, while publish is
skipped because capture did not succeed. This prevents a green run that silently does no work. If the
artifact is missing or expired, recovery stops and requires investigation rather than rebuilding
different bytes under the same version. Candidate artifacts are retained for 90 days.

First-party global and Python documentation are static publication artifacts for the same exact
Release, not Core HTTP routes or wheel contents. Producer tooling obtains documentation addresses
from Registry and must not construct snapshot hosts from the Registry origin. The public deployment
currently routes a broad `*.inkcre.dev` wildcard to Registry so Heroku ACM's wildcard certificate can
serve `registry-docs-{snapshot}.inkcre.dev`; Registry then enforces the exact Host template and
returns 421 for other wildcard matches. Core deployment and Extension packages must not encode that
production DNS compromise or treat DNS/TLS success as proof that a documentation set exists.

`.github/workflows/extension-documentation-publish.yml` is the protected manual publication lane
for first-party producer documentation. It builds every scope under the selected producer with the
pinned shared VitePress toolchain, reads the current Python Distribution's scope ETags, and saves
complete Toolkit candidates before any Registry write. The publish job restores those exact
candidates, revalidates their source against current `main`, publishes them, and checks their public
snapshot origins.
Recovery reruns only the failed publish job; rerunning all jobs is rejected because it would create
new snapshot identities. Candidate artifacts are retained for 90 days.

Official production publication and deployment jobs require
`github.repository == 'InKCre/impl-py'`. A fork may build and test normally, but it cannot enter the
official production environment merely because its `main` branch published an artifact. Fork
owners deploy through the explicit Render or Heroku self-host workflow instead.


## Async Host compatibility window

Core Host 0.2 uses Runtime SDK async startup and persistence capabilities. Host operations and
Extension config/state mutations must be awaited; in-memory config projection and typed mutation
callbacks remain synchronous. ExtensionStateService owns each short business transaction;
app/persistence/extension owns SQL and session creation. Row locks serialize state transforms. A
participation row owns only one Peer’s exact selection, enabled intent, and version-scoped config
schema; shared Extension config and state remain deployment-wide.

Registry-origin database reads finish before Registry HTTP and wheel acquisition. The existing
blocking artifact clients run in a worker without a database session. Startup failure or cancellation
withdraws the current publication and releases its runtime claim.

Core Host SDK 0.3 adds `app.http.get_public_http_base_url()` without changing the async lifecycle
or persistence API. Its authority is `app/version.py`, independently of the Core service package
version in `pyproject.toml`; preparing a service Release PR does not change the Host SDK version.

Core Host SDK 0.3.1 adds batched JSON identity lookup and transaction advisory locking to the
existing Graph repository. Twitter archive reconciliation requires `>=0.3.1 <0.4.0`.

Host SDK 0.3.3 adds optional PostgreSQL blob row locks to shared graph namespace locks, Block/Relation row locks and repeatable-read
graph scopes. Existing consumers retain their defaults. The CalDAV Distribution requires
`>=0.3.3 <0.4.0` because its conditional writes depend on these capabilities.

GitHub, Learn English, Mail, RSS, and Telegram retain their SDK 0.2 lower bound and support
SDK 0.3 through `>=0.2.0 <0.4.0`. Memos uses the new HTTP address API and requires
`>=0.3.0 <0.4.0`. Compatibility metadata changes require new immutable Extension
releases; an old wheel's range must not be widened. Before upgrading a deployment, disable affected
old Extensions, adopt the matching Core/Extension releases, restart where a loaded wheel was replaced,
and then enable them. A migrated exact-version legacy intent is not silently removed on failed cold restore.
Python and Module Federation Releases are versioned independently for the same Extension identity.
Publish a new version only for the Distribution whose artifact or compatibility contract changed;
documentation is also attached to the exact native Distribution Release. A Peer selects the
Distribution and version it can execute, so availability of another Distribution does not imply
that the selected one exists.
