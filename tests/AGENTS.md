# Test execution boundaries

- Default collection also includes `scripts/tests/` and the ChatGPT lifecycle suite. CI runs the same collection against disposable PostgreSQL. Put new business scenarios beside their owner so pytest discovers them. Credentialed or external-provider acceptance must be explicitly marked `acceptance`.
- Database fixtures own their rows and bytes. Protocol DELETE may only unpublish information; it is not fixture cleanup. Remove created records by their exact IDs after observing the retained-information contract. Do not leave malformed semantic content for a later whole-info-base maintenance scan.
- A Job receipt does not prove execution. Use the real `JobManager.start` / shutdown lifecycle and read terminal persisted results; keep real-provider quality separate from deterministic control-flow proof.
- Migration tests may create databases or provision cluster-wide roles. Use only the declared disposable cluster and its existing passwords; never borrow a shared development or preview administrator connection. Do not patch obsolete session factories: bind an isolated `Core` at the domain boundary.
