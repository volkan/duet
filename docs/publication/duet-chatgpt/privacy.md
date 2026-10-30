# Duet for ChatGPT privacy notice

Publisher: Volkan Altan.

This notice describes the Duet for ChatGPT package. ChatGPT and connected
tools have their own data handling and account settings.

## Task information

The workflow uses your task, relevant files, check results, and peer replies.
The coordinator passes relevant materials between two native ChatGPT peers.

The local helper writes the task, native peer handles, full replies,
revision fingerprints, turn outcomes, and stop reason to the selected run directory.
These records support turn tracking, review, and exported transcripts.

## Recipients

The package configures no publisher-operated server or telemetry endpoint.
The helper makes no model or network calls. Prompts and files sent to native
peers are processed by OpenAI through ChatGPT under your account and workspace
settings.

If you authorize another connected tool during a task, its own data handling
applies. Information included in a GitHub support issue is visible through
GitHub's public issue system. Send only information needed to describe the problem.

## Retention and user controls

The helper sets no expiration. Run records remain in the selected directory
until you delete them or host controls remove them. Delete exported transcripts
separately when you no longer need them.

ChatGPT conversations remain in your account until you delete them or an
applicable workspace policy removes them. OpenAI schedules deleted chats for
permanent deletion within 30 days, subject to its stated exceptions. Files
saved separately in Library or Space may require separate deletion. See
[OpenAI's chat and file retention details](https://help.openai.com/en/articles/8983778-chat-and-file-retention-in-chatgpt).

Select only the files needed for the task. Review exported artifacts before
sharing them. Remove credentials, private task text, peer replies, and native
session handles from public support reports.

## Support

Use the [Duet issue tracker](https://github.com/volkan/duet/issues) for package
problems or questions about this notice. Do not include private task records
in a public issue.
