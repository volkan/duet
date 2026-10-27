# Duet for ChatGPT publication materials

The page files in this folder are unpublished drafts for the publisher's review.
They are not listing URLs. Do not claim that a privacy policy is published
until the chosen host serves the approved page publicly.

The publisher confirmed these values on 2026-10-26:

- Publisher name: Volkan Altan. The portal must verify the selected identity.
- Countries: all countries supported by OpenAI, encoded as `publication.countries: []`.
- Commerce: no payments or purchases, encoded as `review.commerce: false`.

The public package uses the existing public project and issue tracker for
`websiteURL` and `supportURL`. Their repository owner and issue availability
were checked through GitHub. Privacy and terms URLs remain absent because the
publisher has no published pages for them.

The current [OpenAI submission reference](https://developers.openai.com/plugins/deploy/submission#manifest-fields)
does not require all four listing URLs for skills-only ZIP metadata validation.
The [plugin guidelines](https://developers.openai.com/plugins/plugin-guidelines#privacy-policy)
still require a published privacy policy. Upload validation and final public
review readiness are separate checks. The cached submission skill's blanket
four-URL instruction is stricter than the current skills-only format reference.

## Listing draft

Name: Duet for ChatGPT

Subtitle: Two native ChatGPT peers

Publisher: Volkan Altan

Category: Developer Tools

Description: Coordinate a reviewer and coder inside ChatGPT Work. Alternate
native peer turns, preserve their host handles, track findings and checks,
and require consecutive rationale-backed agreement on the same revision.
The bundled Python helper records state without model or network calls.
The host must provide native subagents and Python file execution.

The three starter prompts, release notes, confirmed country targeting,
and no-commerce declaration are maintained in
[`packaging/duet-chatgpt/openai.json`](../../../packaging/duet-chatgpt/openai.json).
Review prompts describe intended workflows. They have not been run inside
ChatGPT; local fixture tests do not establish native host acceptance.

## Icon

The generated icon is
[`packaging/duet-chatgpt/assets/icon.png`](../../../packaging/duet-chatgpt/assets/icon.png).
It is a 1254 × 1254 PNG, below 5 MiB. The public ZIP uses it for both `logo`
and `composerIcon`. The white paired speech mark remains clear on its blue
background in light and dark surroundings. No separate dark variant is included.

Built-in image generation produced the icon. The prompt was:

```text
Create a square app-directory icon for the independent developer workflow
Duet. Two equal white rounded speech-bubble silhouettes form a balanced
interlocking pair on a vivid blue background matching #2563EB. Keep the
mark flat, minimal, centered, and legible at 48 pixels. Use no text, arrows,
watermark, or OpenAI or ChatGPT logo.
```

## Remaining publication work

Review the privacy, terms, and support drafts before publishing them.
Choose their hosting destination, then verify their public pages and add the
actual URLs to the public metadata. No hosting service was selected or deployed.

Install and run the [ChatGPT acceptance task](../../CHATGPT_PLUGIN.md#validation)
against the packaged version. The portal's identity checks and policy
attestations must be completed by the authorized publisher. No upload,
submission, or publication has occurred.
