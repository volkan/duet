# Duet for ChatGPT support

Report package problems through the
[Duet issue tracker](https://github.com/volkan/duet/issues).

Include the package version, host surface, stop reason, steps to reproduce,
and the relevant error. Remove credentials, private task text, peer replies,
and native session handles before posting.

For `capability_unavailable`, check whether the selected ChatGPT Work host
provides native subagents, persistent handles, follow-up turns, and Python
file execution. The package cannot enable missing host capabilities.

For a local helper failure, retain the private run directory and inspect
the curated `status` output. The transcript contains full peer replies;
review it before sharing any excerpt.
