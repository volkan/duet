# Duet for ChatGPT

Duet for ChatGPT coordinates a reviewer and a coder inside ChatGPT Work.
It uses two native ChatGPT peers and the host's internal model routing.

The peers alternate turns within a fixed limit. A local Python helper records
their replies, inspected revisions, and stop reason. Agreement requires two
consecutive approvals for the same input fingerprint.

The host must provide native subagents, persistent peer handles, follow-up
turns, completed replies, and Python file execution. The workflow stops when
these capabilities are unavailable. Effective model settings remain unknown
when the host does not expose them.

Publisher: Volkan Altan.

[Source project](https://github.com/volkan/duet) · [Support](support.md) ·
[Privacy](privacy.md) · [Terms](terms.md)
