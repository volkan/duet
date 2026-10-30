# Duet for ChatGPT privacy notice

Publisher: Volkan Altan.

This notice describes the Duet for ChatGPT package. ChatGPT and connected
tools have their own data handling and account settings.

## Task information

The workflow uses the task, relevant files, check results, and peer replies
to review or produce the requested work. The coordinator passes relevant
materials between two native ChatGPT peers.

The local helper writes the task, native peer handles, full replies,
revision fingerprints, and stop reason to the selected run directory.
These records support turn tracking, review, and exported transcripts.

## Recipients

The package configures no publisher-operated server or telemetry endpoint.
The helper makes no model or network calls. The ChatGPT host processes native
model requests and controls the task environment.

If you authorize another connected tool during a task, its own data handling
applies. Information included in a GitHub support issue is visible through
GitHub's public issue system. Send only information needed to describe the problem.

## Retention and user controls

The helper does not schedule automatic deletion. The host's workspace rules
can remove task files. You can delete saved run files and exported transcripts
with the host's file controls. Host account storage follows its service settings.

Select only the files needed for the task. Review exported artifacts before
sharing them. Remove credentials, private task text, peer replies, and native
session handles from public support reports.

## Support

Use the [Duet issue tracker](https://github.com/volkan/duet/issues) for package
problems or questions about this notice. Do not include private task records
in a public issue.
