# Self-Hosting On Render And Neon

## Purpose And Boundary

The checked-in `Deploy self-hosted InKCre` workflow gives a repository owner a browser-only
path from a public `core-py` fork to their own InKCre deployment. It creates or converges
two Render Docker web services against the owner's Neon project:

- `RENDER_SERVICE_PREFIX-core` runs the complete `core-py` Peer;
- `RENDER_SERVICE_PREFIX-postgrest` exposes the executable peer database contract.

Forking is only the distribution and onboarding mechanism. It does not make the resulting
deployment a projection of InKCre's canonical production environment, and business code
does not branch on whether a deployment is self-hosted. This profile currently selects
Render Free instances, whose sleep behavior means scheduled work and Peer leases are not
continuously available. Wake the core URL and wait for `/readyz` before an interactive
session. A stronger hosting plan can improve availability without changing domain behavior.

The current HS256 `JWT_SECRET` grants full admitted-Peer authority. Keep it private. Do not
put it in a repository variable, README, deployment profile, App build, issue, log, or
workflow summary. A public read-only experience needs a different admission surface; a
different role name beside the same signing key does not reduce the key holder's authority.

## Browser-Only Onboarding

No local clone is required.

1. Fork this repository and enable GitHub Actions for the fork.
2. Create a Neon project and an API key that can access the project's account or organization.
   Its default branch must retain the standard `neondb` database and `neondb_owner` role.
   Copy the project ID from Neon.
3. Create a Render workspace and API key. Copy the workspace ID from the workspace Settings
   page. The fork must be publicly readable by Render, or the Render workspace must already
   be authorized to access it.
4. In the fork, open **Settings → Secrets and variables → Actions** and add the following
   repository settings.

| Kind | Exact name | Meaning |
| --- | --- | --- |
| Secret | `NEON_API_KEY` | Can resolve and mutate `NEON_PROJECT_ID` in its account or organization |
| Secret | `RENDER_API_KEY` | Can create and configure services in the selected workspace |
| Secret | `JWT_SECRET` | At least 32 bytes; owner-only Peer signing authority |
| Variable | `NEON_PROJECT_ID` | Target Neon project identity |
| Variable | `RENDER_OWNER_ID` | Target Render workspace identity |
| Variable | `RENDER_SERVICE_PREFIX` | Unique 3–40 character lowercase service prefix |

A password manager can generate the JWT value; it does not need to be derived from another
deployment secret. Do not reuse a provider API key or database password as the JWT secret.

5. Open **Actions → Deploy self-hosted InKCre → Run workflow** and select the commit/branch to
   deploy.
6. Read the workflow summary for the non-secret core URL, PostgREST URL, Peer ID, and exact
   commit. Keep using the repository's private `JWT_SECRET` when an admitted Peer asks for
   the signing key.

## What The Controller Owns

[`scripts/deploy_render_neon.py`](../../scripts/deploy_render_neon.py) is the deployment
controller for this exact provider profile. The workflow resolves the Neon default branch's
direct and pooled owner URLs, then invokes that controller. In order, it:

1. validates all owner inputs before a provider mutation;
2. finds the two exact service names;
3. generates database-role passwords on first deployment, or recovers them from the
   owner-authorized Render environment on later runs;
4. migrates and converges the executable database contract before deploying either runtime;
5. creates or updates two auto-deploy-disabled Render Free Docker services without replacing
   unrelated owner-authored environment variables;
6. waits for an exact-commit deployment of each service;
7. probes core readiness, then waits until an anonymous PostgREST request reaches the runtime
   and returns the expected `401` admission boundary;
8. converges the core Peer's public inbound advertisement and runs the authenticated
   PostgREST read/write/deny contract once.

The database owner URL and standalone role passwords are never placed in a Render service.
Only role-specific runtime URLs are retained there. Workflow output and summaries contain
only public service coordinates and source identity.

The PostgREST availability probe is safe to retry while Render is still routing a new
hostname. The complete verifier remains one-shot because an interrupted read/write contract
may already have produced partial writes.

Rerunning the workflow is convergence, not credential rotation. If an existing managed
service has lost its role-specific URL, the controller stops instead of silently changing
the database credential. A same-name service without the controller's ownership marker is
treated as a collision rather than adopted. The controller also refuses to reuse the same
service prefix for a different database coordinate. Use a different prefix for a second
deployment, or perform an explicit, coordinated rebind outside this workflow. Intentionally
rotating credentials remains an owner operation and must update the database and service
together.

## Free-Host Runtime Limits

- Render Free services can sleep independently. A browser request can wake PostgREST, but an
  expired database-published core lease cannot activate a sleeping core process. Open the
  core `/readyz` endpoint first.
- Missed Cron occurrences remain missed; waking the service does not synthesize catch-up
  jobs.
- The workflow serializes deployments per fork and disables Render auto-deploy. GitHub
  workflow dispatch is the delivery authority for the selected exact commit.
- The deployment controller does not roll back a failed first deployment. It preserves the
  database and service state so a corrected rerun can converge it.

Render documents its Free-instance sleep behavior and quotas in [Free
instances](https://render.com/docs/free), and the service/API fields used here in its
[Infrastructure as Code](https://render.com/docs/infrastructure-as-code) and [public API
reference](https://api-docs.render.com/reference/create-service).

## Cleanup

Delete the two exact Render services when the deployment is no longer wanted. Delete the
Neon project only if it is dedicated to this deployment and its data is disposable. Deleting
either is an owner-controlled destructive action; the workflow intentionally does not infer
cleanup authority from an ordinary deployment run.
