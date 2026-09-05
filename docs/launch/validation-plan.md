# Validate the review-verification workflow

This is a proposed product and outreach experiment based on the
[September 2026 usage audit](../USAGE_EVIDENCE.md). The audit covers one developer;
the experiment is intended to learn whether other developers find the workflow
useful. It makes no claim about current market size or comparative model quality.

## Audience and message

Start with developers who already use an AI agent to draft code reviews and
still spend effort checking its claims before sharing them. The hypothesis is
that a second agent's challenge, tied to inspectable evidence, helps them decide
which comments deserve attention.

Test this message:

> Verify your AI review before you share it.

Describe the result concretely: a report showing each claim, its evidence,
corrections, and unresolved objections. Describe the two-agent mechanism after
showing that result. The [finding workflow](../FINDINGS.md) now produces a local
report, and the [toy fixture](../../examples/review-evidence/README.md) supplies
repeatable evidence. Label authored expectations and mock-ups clearly; show
actual report output when demonstrating agent behavior.

## First deliverable

Prepare a small public repository or fixture using fresh toy code and invented
names. Include an initial review with a plausible but incorrect claim, a real
issue that needs attention, and a claim the fixture does not provide enough
evidence to resolve. Author expected outcomes from the code and explicit checks
before running the agents.

Run the same review through Duet and retain the complete result. Show a specific
claim, the partner's challenge, and a repeatable check in a short recording, with
the full fixture and commands linked beside it. Include unresolved findings and
unsuccessful runs in the accompanying record. A fixture demonstrates behavior;
its handpicked cases do not estimate a real-world error rate.

The [80-second edited demo](../demos/README.md) now includes offline playback,
a shareable MP4, both captured finding reports, model and reasoning metadata,
and the public fixture source. Its expected assessments were visible to the
agents. It demonstrates the workflow and makes no claim about blind discovery
or comparative quality. The next validation step is the small pilot below;
no participants have been enrolled by this work.

The demonstration should let a developer reproduce the check, inspect the
evidence, and understand the remaining uncertainty. Use no workplace code,
original project or folder names, or raw private audit excerpts.

### Which demo to show first

Lead with one clickable preview of the review workflow in the README, beside
the brief explanation and quick start. It shows the developer's result: a
supported finding, a refuted concern, and a question that remains unresolved.
Keep the real work cases available for context and identify the recording's
public toy fixture explicitly.

Retain `duet-deck.gif` as a secondary link in the demo guide for readers who
want an explanation of roles, memory, and agreement. Its older implementation
details make it unsuitable as the primary reference. Avoid embedding both
media in the README. A static preview lets readers choose when to play;
controllable playback also follows [W3C guidance on pausing moving content](https://www.w3.org/WAI/WCAG22/Understanding/pause-stop-hide.html).

This ordering is a design hypothesis. During the pilot, ask a first-time
reader to explain what Duet produces, what an unresolved finding means, and
how to start one review. Record confusion and whether the overview helped;
clicks or completed playback alone do not establish understanding.

## Small pilot

The CLI now supports optional `--feedback RUN --usefulness ... --decision ...`
records without free text or code. This is collection infrastructure; no pilot
participants, acceptance rates, or repeat-use outcomes are implied.

Proposed initial scope: five to ten developers over two weeks. These are planning
targets, not existing users or evidence thresholds. Let each participant bring
an appropriate review task and keep its contents locally.

Collect an optional short outcome record for each task:

| Observation | What it helps assess |
|---|---|
| Setup completed and first result inspected | Whether onboarding reaches a usable result |
| Useful finding accepted or inaccurate comment corrected | Whether the report changes a review decision |
| Finding rejected, unresolved, or result abandoned | Where the workflow adds work or fails to help |
| A second task started voluntarily | Whether value persists beyond the demonstration |
| Measured elapsed time and any reported cost | Whether the participant considers the effort worthwhile |

Record attempted tasks and failures as well as completed tasks. Ask which
evidence changed the participant's decision. Treat self-reported usefulness,
human acceptance, and independently checked correctness as separate observations.
Elapsed time alone is not a time-savings estimate.

Where a comparison is practical, use the same starting code and review input for
a single-agent verification pass and for Duet. Record model settings, available
tools, and time or turn budgets for both. Have a person assess the resulting
claims against repository evidence without being told which method produced
them. A small pilot will still be exploratory, not a quality benchmark.

## Attract attention with a result people can inspect

Publish the reproducible fixture and a short claim-to-evidence demonstration on
the project page. Share that concrete example in developer communities where
review workflows are already discussed, following their posting rules. Invite
people to try one review task and report which claim changed their decision.
These are proposed outreach actions; this document does not send invitations or
publish posts.

Link the usage evidence with its one-user scope when explaining why this
workflow was chosen. Avoid agreement-as-success percentages, unreconstructable
refutation rates, model superiority claims, or implied workplace endorsements.

After the pilot, compare attempts, usable results, changed decisions, repeat use,
and participant effort. If people cannot reach a first result, improve setup.
If they inspect the report but cannot act on it, improve evidence and unresolved
finding presentation. Expand outreach when multiple participants voluntarily
return and can explain the value; if that signal is absent, revise the workflow
before broadening the pitch.
