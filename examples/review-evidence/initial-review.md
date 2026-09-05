# Illustrative initial review

These are authored review claims for the invented `toy.py` fixture. They are
not output from an agent run or evidence of agent performance. The assessment
labels in `expected.json` are the assessments a reviewer can justify from this
fixture's evidence; they are not independent universal truth labels.

## L1 — A partial batch is incorrectly counted as full

Assessment: supported.

`plan_batches` promises to count only complete batches, but its ceiling-division
expression returns three for five items at capacity two. Only two complete
batches fit. Run `check_fixture.py` to reproduce the mismatch. A scoped fix is
`len(ordered) // capacity` for `full_batches`.

## L2 — Sorting changes the caller's list in place

Assessment: refuted.

This is a deliberately plausible initial concern to check, not an established
defect. The implementation calls `sorted(item_ids)`, which creates a separate
list. The fixture passes an unsorted list, retains its original order, and
checks that it is unchanged after the call. A reviewer should retract this
comment; changing the implementation to address it is unnecessary.

## L3 — Production requires natural item-ID order instead of lexical order

Assessment: unresolved.

The fixture can establish that `item-10` sorts before `item-2`. It cannot
establish whether production requires lexical order, natural numeric order,
or another ordering policy: no production requirements or downstream consumer
are supplied. Obtain that acceptance criterion before calling the ordering a
defect or proposing a change. The deterministic check records the missing
evidence; it does not adjudicate the external requirement.
