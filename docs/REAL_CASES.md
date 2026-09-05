# What Duet changed in real work

These examples are paraphrased from a supplied work-computer audit dated
5 September 2026. They describe one developer's use, with project names,
folder names, code, and original dialogue omitted. The source transcripts
and repositories were unavailable for independent inspection; the outcomes
below are what the audit reports.

## A fix stopped hiding sensitive values

An agent replaced a text-matching pattern that could take too long to run.
It reported the requested fixes complete.

The other agent challenged the replacement: for some ordinary inputs, it
matched nothing. A value that was previously partly hidden would now be
written out in full. A newly added test even treated that output as correct.

The implementing agent accepted the objection, revised the pattern while
keeping its runtime bound, and changed the test to reject the exposed value.
Both agents agreed after five turns.

**What changed:** the proposed code and its test, after the first agent had
already called the work complete. The audit describes the objection as code
reading and hand-tracing, with test execution recorded on the implementing
side. It does not include a captured reproduction of the defect or establish
that the fix was merged or deployed.

## An allegedly impossible dependency failure had happened

One agent argued that two incompatible libraries had never appeared together
in a reachable committed state, so the proposed explanation for a failure
was unsupported.

The other agent checked more of the repository's history. It identified
missed commits and a released tag containing the incompatible pair.

The first agent withdrew its objection. The audit reports actual history and
tag-inspection command traces on the responding side; both agents agreed
after five turns.

**What changed:** an incorrect rebuttal was withdrawn after a more complete
history check. The audit does not establish how a person used the result.

## A review comment became a cleanup suggestion

A reviewer flagged unused declarations in an infrastructure definition as
issues that should be addressed.

The other agent argued that the declarations came from a shared template
and followed an existing pattern. It asked for the comments to be treated as
optional cleanup instead of blocking the change. The reviewer accepted that
framing, and the agents agreed after five turns.

**What changed:** the priority of the review comments. The decisive comparison
with other definitions was an agent assertion without a corroborating trace
in the audit. This illustrates reconsideration, not proof that the lower
priority was correct.

## Where the second opinion was not enough

- **A corrected review still needed confirmation.** In a six-turn run, agents
  repeatedly disputed line references and which branch the review described.
  An objection on turn five left only one turn for corrections. The draft
  changed, but the other agent never got to confirm it. The run ended
  unresolved.
- **A routine update gained no visible new insight.** An already-approved
  dependency lock-file update went through two more turns. The agents
  repeated existing points and produced no new finding, correction, or
  change. Agreement alone added no visible value in this selected case.

## What this evidence supports

These selected cases explain why someone might use a second agent to inspect
a proposed fix or challenge a review before acting on it. They do not measure
how often Duet helps, which model is better, or how much time it saves. None
has a harness-run verification result; that is narrower than the agent tool
use described above. Human acceptance, merging, and deployment are not
confirmed by these records.

The [usage evidence note](USAGE_EVIDENCE.md) covers the broader audit, counting
method, and limitations. The [recorded workflow demo](demos/README.md) uses a
separate public toy fixture to make the report and continuation commands
reproducible; it does not replay these private cases.
