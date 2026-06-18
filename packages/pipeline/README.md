# cognitio-pipeline — Layer 3, Pipeline

The pipeline is a Postgres-backed, idempotent job DAG.

| Job | Follow-ons |
|---|---|
| `fetch` | `normalize` when a new source version is committed |
| `normalize` | `chunk` |
| `chunk` | `invalidate`, `embed`, and `extract` for changed chunks |
| `embed` | none |
| `extract` | `entity_resolve` |
| `entity_resolve` | none |
| `invalidate` | `extract` for each stale record |

Completing a job and enqueuing follow-ons is one storage transaction.
