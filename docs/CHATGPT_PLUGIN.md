# Duet for ChatGPT

This plugin runs a reviewer and a coder inside ChatGPT Work. It uses native
ChatGPT subagents and internal models. Its Python helper records turns and
agreement without making model or network calls.

The package is separate from the CLI plugin because it has different host
requirements and execution controls. Source lives in
[`plugins/duet-chatgpt`](../plugins/duet-chatgpt/). Its `0.1.0` package version
is independent of `duet-cli` and the existing native CLI plugin versions.

## Host requirements

Use ChatGPT Work with native subagents, persistent peer handles, follow-up
turns, completed reply access, and Python 3.9+ file execution. Both peers
must receive the relevant files and check evidence. The skill checks these
capabilities before starting. It stops when a requirement is unavailable.

OpenAI documents [ChatGPT Work subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
and [skills and plugins](https://learn.chatgpt.com/docs/skills-and-plugins).
Availability depends on the account and host. At most intelligence levels,
request subagents explicitly. The plugin's default prompt includes that request.

Use the host's internal model routing. Specific model and intelligence
requests require native controls that support them. Unexposed effective
settings remain unknown. The package does not select model IDs through an API.

## Build and install privately

From this checkout:

```sh
make chatgpt-plugin-check
make chatgpt-plugin
```

The build writes `dist/duet-chatgpt-0.1.0.zip`. It verifies every archive
member against the source. The ZIP contains the portable manifest, license,
skill, peer brief, and local state helper. It excludes the CLI, MCP servers,
credentials, marketplaces, and account bindings.

## Prepare an OpenAI public upload

Build the separate public ZIP from the same source:

```sh
make chatgpt-plugin-public
```

The output is `dist/openai/duet-chatgpt-0.1.0-public.zip`. Its portable manifest
includes the listing icon, three starter prompts, release notes, the confirmed
publisher name, all-country targeting, and the no-commerce declaration.
The public build leaves the private package's metadata unchanged.
Public settings and icon source live under `packaging/duet-chatgpt/`.

The [OpenAI submission guide](https://developers.openai.com/plugins/deploy/submission)
distinguishes upload validation from final review. Skills-only ZIP metadata
does not require all four site URLs or MCP cases and demo materials.
A primary icon is required before submission. The plugin guidelines still
require a published privacy policy and verified publisher identity.

[Publication materials](publication/duet-chatgpt/README.md) contain the listing
and unpublished page drafts. Their absence as public URLs is disclosed;
the package does not contain invented privacy or terms addresses.
Host acceptance and publisher attestations remain separate from ZIP validation.

## Install privately

For a local desktop installation, this repository's existing marketplace
lists `duet-chatgpt` beside `duet`. Open the checkout in the ChatGPT desktop
app, restart the app, then open Plugins. Choose the **Volkan Duet** repository
source and install **Duet for ChatGPT**. Follow OpenAI's
[local marketplace instructions](https://developers.openai.com/plugins/build/plugins#install-a-local-plugin-manually)
if the repository source is not visible. This route makes the local source
discoverable; it does not upload the package to a web or mobile account.

For an account installation, give the private ZIP to Plugin Creator in
ChatGPT Work and ask it to save the package privately. Saving requires the
host's supported account creation tool. If that tool is unavailable, account
installation remains pending. Do not create an MCP server to replace it.

After installation, choose **Duet for ChatGPT** in a Work chat and send:

```text
Use Duet for ChatGPT. Spawn two native ChatGPT subagents, one reviewer and
one coder. Use only ChatGPT's internal models. Review the supplied files and
implement the fixes I have authorized. Alternate up to six peer turns.
```

Supply the actual files and acceptance criteria. For review-only work,
replace the implementation sentence with `Review only; do not edit files.`

## Execution and evidence

The coordinator creates two fresh peer contexts and pins their native
handles. The reviewer starts, and only one peer turn runs at a time. Each
peer receives the latest full partner reply. Neither peer may create agents.
These are workflow instructions, not host-enforced tool restrictions.

The state helper enforces distinct pinned handles, turn order, a fixed
turn limit, and terminal stop reasons. It accepts agreement only after two
consecutive successful peer approvals for the same input fingerprint.
Each approval needs a substantive rationale before a standalone sentinel.
Use a final top-level approval paragraph with the sentinel last. Quoted or
indented examples, fenced text, bare sentinels, and reversed rationale do not
count. This final-paragraph rule is stricter than the CLI parser.

Fingerprint the complete relevant input set. Include the artifact, source,
configuration, dependencies, fixtures, target, commands, and check results.
Use stable relative names. The helper hashes file names and contents, so
changes invalidate the earlier approval pair. The coordinator must verify
that the supplied replies and fingerprints correspond to actual native work.
The helper cannot authenticate a model, peer handle, or agent claim.

Checks and finding dispositions remain separate from agreement. Preserve
unresolved findings, per-peer assessments, and evidence provenance. Report
executed checks separately from peer claims and unrun acceptance checks.

The private run directory contains `state.json`, including the task, native
handles, and full replies. Keep it with the task's private artifacts. The
`status` command excludes those contents. Render a readable record with
`transcript`; it reads the authoritative state without changing it.

The helper does not execute agents or cancel them. User stops require a
native cancellation request when available. Errors and timeouts end this
version's run. A `verification_failed` stop also invalidates a recorded
`converged` verdict without reopening turns. A stale `.control.lock` blocks writes; inspect the owning
coordinator before removing it. Do not steal a live lock.

Saved state does not restore native peer memory. A continuation after losing
handles requires a new run and fresh contexts. Identify its earlier evidence
as inherited and reset approval votes.

This package does not provide CLI worktrees, process-group cleanup, enforced
subprocess timeouts, CLI schema-1 status, central metrics, or backend adapters.
The existing [CLI plugin](CODEX_PLUGIN.md) remains the route for that workflow.

## Validation

Run the local gates against this checkout:

```sh
make ci
make package-check
make plugin-check
make chatgpt-plugin
make chatgpt-plugin-public
```

`make ci` includes native-control regression tests, a subprocess control-plane
smoke test, and private package validation. Package tests inspect the exported
ZIP and reject unexpected files, symlinks, and missing references.
`make package-check` also extracts the built source distribution and runs its
full unit suite. `MANIFEST.in` includes the helpers and native plugin resources
used by those tests, so test collection does not depend on the checkout.
These checks do not establish successful installation or native inference
inside ChatGPT.

After installation, run this acceptance task in ChatGPT Work:

```text
Use Duet for ChatGPT with two native ChatGPT subagents and internal models
only. Create clamp.py with clamp(value, lower, upper). Return the nearest
bound outside the interval, return value inside it, and raise ValueError
when lower exceeds upper. Add and run focused tests. Alternate the reviewer
and coder for at most six peer turns, then show the transcript, actual model
settings when exposed, check output, fingerprints, and stop reason.
```

Confirm two distinct native handles, reviewer-first alternation, independent
review of actual files, passing checks, and a matching-revision approval pair.
Also check a review-only request and a host without native subagents. The
former must preserve files; the latter must report `capability_unavailable`.
Record installation and these host results before calling the plugin verified
in ChatGPT.
