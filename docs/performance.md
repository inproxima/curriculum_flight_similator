# Measured performance (development machine)

Measured 2026-09-26 on the developer's Mac (Apple Silicon; Docker Desktop for PostgreSQL). These numbers come from
the **synthetic** stress fixture (`python -m cfs.fixtures.stress --bench`). They are measurements, not guarantees.

Fixture: 150 courses, 1,112 entities, 2,098 relationships (3 outcomes and 2 assessments per course, 200 topics,
12 program outcomes, AND/OR prerequisites, inferred edges).

## Backend (Python, in-process; median of 3–5 runs)
| Operation | median ms | max ms |
|---|---:|---:|
| Load snapshot from PostgreSQL | 87.3 | 93.9 |
| Graph view, overview (150 nodes, 211 edges) | 0.9 | 1.5 |
| Graph view, all layers (300 nodes, 923 edges) | 2.0 | 21.4 |
| Prior learning, final-year course | 0.3 | 1.1 |
| Downstream, first-year course | 0.1 | 0.1 |
| Outcome matrix | 2.3 | 2.4 |
| Issue detection | 18.9 | 19.1 |
| Scenario analysis, remove 3 courses (38 findings) | 103.5 | 104.1 |

Before indexing relationships on the snapshot, the outcome matrix took 309 ms and scenario analysis took 1,382 ms.

## HTTP and browser (Chrome, Vite dev server in Docker)
| Measurement | ms |
|---|---:|
| `GET /versions/{id}/graph` for 150 courses (curl, 3 runs) | 73–127 |
| Graph fetch as seen by the browser (`graph_fetch`) | 92 |
| ELK layout, 150 nodes / 211 edges, first run (includes ELK worker startup) | 921 |
| ELK layout, same graph, warm | 240 |
| Selection → details panel populated (`selection`) | 8 |
| Synthetic program (25 courses): layout, warm | 211 |

Browser timings are recorded in `window.__cfsPerf.all()`.

## Budgets
- Keep course-level overview layout under 1 s cold and 300 ms warm at 150 courses.
- Keep deterministic scenario analysis under 250 ms at 150 courses (it runs synchronously in the request).
- The graph view has a visible-node budget (default 300). Above it, the response is truncated with an
  explanation, and the UI points to filters, focus, or the table view.
