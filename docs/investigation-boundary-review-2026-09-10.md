# Investigation scope and follow-up work

## Root cause

The task contract previously converted every non-blocking Unknown into the
deferred strategy. The investigator and inspector then used the same collection
for current questions, postponed work, traversal and completion reporting.
This conflated priority, capability and completion.

## Data boundary

`stratumcode/followups.py` partitions explicitly deferred work into a separate
`follow_ups` ledger. Each transferred item retains its original question, parent,
acceptance links, reason, required capability and any existing resolution.
Non-blocking investigative questions are no longer implicitly deferred.

The ledger survives findings merges and continuation and travels with live tree
snapshots and final results. It is not part of traversal, closure or tree progress.
If a transferred ancestor has investigative descendants, those descendants remain
in scope under the nearest in-scope ancestor. This does not discard their work.
An intentionally follow-up-only task does not synthesize a replacement root.

The inspector presents a separate Follow-up verification section and projects
legacy deferred nodes into it. These entries are not evidence, not completed
investigation questions, and not automatically scheduled background jobs.
Execution ownership and background scheduling remain future architecture work.

## Related runtime fixes

Related observations no longer make evidence sufficient by definition: resolve
and synthesis phases retain read tools. Successful partial resolution releases
the obsolete sticky resolve lock. A no-material-findings checkpoint can acknowledge
reviewed observations without inventing beliefs. Root audit can explicitly reopen
a closed question when further evidence is needed.

Light answer provenance is derived from the coordinator's actual tool ledger,
not model-authored copies of nested observation IDs. Delegation success is still
checked independently; partial work is not promoted to a complete result.
