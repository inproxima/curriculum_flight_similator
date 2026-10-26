"""Course outline import (ADR 0004): upload or pick an outline for one course, parse it, let an agent reconcile it
with what the draft version records for that course, verify every decision in code, and apply the reviewed
selection in one transaction that can be reverted.

    edition   which academic year / date an outline and each existing element belong to ("newer replaces older")
    state     the course subgraph a plan was built on (staleness)
    missing   the pieces the program lacks for one course
    agent     the reconcile call (route `synthesize`)
    verify    deterministic checks of the agent's output
    policy    what a plan item may do and whether it is pre-selected
    plan      plan items, hashes, the ETag
    project   the plan applied to a snapshot copy (preview of missing pieces)
    apply     writes a selection to the draft version, with an undo log; revert
    job       the `outline_import` job
    service   create, rerun, discard
"""
