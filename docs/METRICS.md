# Central metrics

Duet can keep a small local record of run performance after the raw run
directory is removed. The record is deliberately a measurement snapshot, not
a transcript archive or telemetry channel. Nothing is uploaded automatically.

## Storage and controls

Enabled snapshots are written atomically to:

```text
~/.duet/metrics/runs/<UUID>.json
```

This path is independent of `~/.duet/runs`, which remains the legacy run index
for `--list`, status lookup, and raw run discovery. Raw transcripts, state,
worktrees, and stderr logs continue to use the configured `--runs-dir`.

Collection is enabled by default unless the environment sets
`DUET_METRICS=0`. Disable one run with `--no-metrics`, or set
`metrics_enabled: false` in YAML/JSON. Set `--metrics-kind test` or
`metrics_kind: test` for a manual evaluation; the default is `live`. Dry runs
are always classified as `dry_run` and are kept separate.
For CLI launches, `DUET_METRICS=0` takes precedence over config files and
settings restored by `--continue`, including `metrics_enabled: true`.

If the home metrics directory cannot be created or written, duet prints a
warning and continues the run. A failed snapshot must never block agent work.
Snapshot writers use a nonblocking lock per UUID so a refresh cannot replace
a newer live snapshot with stale data. Lock contention is recoverable with a
later refresh. Deleting the metrics directory removes these local records;
disabling collection does not delete records already collected.

## Snapshot contract

Each file is a JSON object with `schema_version: 1` and
`kind: "duet.metrics.run"`. The stable top-level fields are:

| Field | Meaning |
|---|---|
| `id` | UUID snapshot identifier |
| `source` | `recorded` for a new snapshot or current-state recovery, `legacy` for an explicit refresh import |
| `run_kind` | `live`, `test`, `dry_run`, or `unknown` |
| `duet_version` | Duet version when known |
| `phase`, `finished_reason` | Curated lifecycle outcome; missing values remain unknown |
| `agents` | Safe profile for each slot: backend, built-in/custom role, requested model, bounded CLI version, reasoning requested/effective/backend value and transport, prompt transport, fast-mode, and whether unrecorded extra arguments were present |
| `max_turns`, `per_turn_timeout` | Configured run budget when known |
| `wall_elapsed_s` | End-to-end elapsed time when available |
| `turns` | Numeric timing/byte fields and safe outcome labels for each turn |
| `verification` | Verification attempts, passes, and failures |

Turn snapshots may include `agent_elapsed_s`, `verify_elapsed_s`,
`system_prompt_bytes`, `partner_message_bytes`, `raw_output_bytes`,
`delivered_output_bytes`, and `handoff_bytes`. Provider metadata is optional:
`model_reported` or a bounded `models_reported` list, `usage` (provider token
counters), `usage_scope`, and `cost_usd`. Token and cost totals are only
compared when `usage_scope` is `invocation`; session or unknown values remain
unpooled. Provider coverage differs, so absent fields are unknown; duet does
not estimate tokens from bytes or infer prices. `system_prompt_bytes` and
`partner_message_bytes` measure Duet's input strings before a backend adds its
own prefix or combined-prompt framing; they are not provider input-token or
full-wire-byte measurements.

When available, `usage.reasoning_tokens` preserves a backend's native
reasoning/thought counter. It is not normalized across vendors and must not be
blindly added to input or output totals: some provider counters already include
reasoning in another reported number.

The current adapters collect native usage ledgers from Claude's `modelUsage`,
Gemini's `stats.models`, and OpenCode's `step_finish` events. A total stays
unknown if any contributing entry is missing that counter. Codex's current
text transport and Copilot's current result format do not supply a verified
usage ledger to Duet. Their model, reasoning configuration, timing, and byte
measurements are still recorded; provider usage and cost remain unknown.

The snapshot intentionally excludes raw task text, repository or folder names,
filesystem paths, prompts, command text, errors, session IDs, credentials, and
raw agent output. A stable locally salted opaque working-context identifier,
when present, is only for same-machine grouping. It cannot prove that two runs
used the same repository or identify an anonymous user.

## Reporting and refresh

`duet --stats` reads bounded standalone snapshots and prints a human summary.
`duet --stats --json` prints `kind: "duet.metrics.report"`, including record
counts, skipped-file counts, outcome totals, verification and wall-time
coverage, agent groups, and paired-agent groups. Malformed, unreadable,
oversized, unknown-schema, and duplicate snapshots are skipped and counted.
Malformed records include numeric JSON values rejected by Python's integer
conversion limit; one such record does not prevent valid snapshots from being
reported.

Timing medians remain finite even for extreme numeric inputs. If a sum of
reported costs exceeds the finite numeric range, its JSON `total` is `null`;
coverage still counts the supplied measurements. The human report displays
`unknown (overflow)` instead of an infinite or misleading partial total.

`duet --stats --refresh` explicitly imports discoverable older `state.json`
files, then reports. The no-argument form uses the normal default roots and
known home index. `duet --stats --refresh PATH` imports one explicit runs root.
Refresh does not execute task, verification, or agent commands and does not
edit the original run directories. Imported records are deduplicated.
Invalid timestamps, including timezone conversions outside the supported year
range, become unknown without preventing other metrics from being imported.
Refresh also repairs missing or stale snapshots from new-format states;
the imported count includes these updates. It cannot reconstruct artifacts
that were already deleted before metrics were collected.

Older states may not contain enough information to identify the historic Duet
version, budget, or live-versus-dry classification. Those values remain
unknown. Legacy records are counted separately and excluded from default live
performance comparisons; `dry_run`, `test`, unknown-kind, and incomplete live
records are likewise excluded from those comparisons.

## Reading comparisons responsibly

Reports exclude incomplete live runs from performance aggregates and group
observations by versions, backends/models, effective reasoning, roles, turn
kind, fresh/resume mode, usage scope, configured turn/time budgets, and
paired-agent profiles. They expose timing and provider-usage coverage so a
missing value is not mistaken for zero. “Effective” means Duet's intended
emission; unrecorded extra arguments or provider behavior can override or
ignore it, and exact model identity remains unknown unless reported by the
provider. Agent agreement and finished outcomes are observational signals, not
correctness labels. Provider usage scopes can differ, and the data is not a
controlled benchmark of vendors or models. Requested or reported model IDs are
bounded identifiers, not guarantees of anonymity for user-defined aliases.
