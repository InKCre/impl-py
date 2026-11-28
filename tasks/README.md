# Tasks

This directory is the Spoke-local, agent-owned task workspace of the repository.

Every non-trivial task starts with one `packet.md`. Keep its compact,
Human-facing control surface sufficient to recover:

- the outcome and material guardrails
- the terminal claim and how it will be verified
- consequential current truth, decisions, and uncertainty
- the current front or next step at useful resolution
- one Human attention item, only when one exists

Use the installed `svc-task-packet` Skill for packet shape and lifecycle guidance. Start with a small `packet.md` in this directory.

Use this directory for:

- local exploration before durable promotion
- diagnostics before bug fixes
- transient artifacts and one-off execution notes
- temporary reasoning that should not pollute durable docs
- evidence and human-agent collaboration state
- local-pressure capture before promoting a missing shared rule into the Hub repo

Do not treat files here as permanent truth. A Task Packet owns only task-local
state whose persistence, recovery, or sharing lowers coordination cost; it does
not own durable project truth, Working Methods, acceptance, or a runtime work
graph.

A task packet may start as one file. Split adjacent Plans, Cells, Inquiries,
Designs, Decisions, Diagnostic Matrices, or Verification material only when a
distinct owner and real topology, retrieval, or coordination pressure make the
package cheaper to control. Supporting files are opt-in, not mandatory
scaffolding or a completed-work log.

Exclude `tasks/` from ordinary source and durable-doc search unless the active question targets task state or evidence.

Update the semantic information owner first, then task work-control state, then
the short Human projection. Promote only stable, reusable,
expensive-to-rediscover knowledge into durable docs, and keep the owning layer
explicit. At close, check for stranded deltas and material residual; do not
perform an archive or deletion-time promotion ceremony.

Maintain `packet.md` as the current state, not an append-only progress log. After a phase
finishes, a blocker clears or authorization changes, replace the affected current-state and
next-step text in the same update. If a plan or inventory owns detailed status, update that
owner first and keep the packet's projection consistent. Do not leave “pending migration”
beside a later completion entry and expect readers to reconcile them.

Keep dated history only when it explains a decision or verification result; label it as history
and link to it from the current state when useful. Distinguish implementation complete,
checks pending, merged and production delivered. For delivery evidence, identify the verified
commit and run, and distinguish actual deployment from a successful no-op workflow.
These are editing rules for the task owner, not a new task schema or keyword lint gate.
