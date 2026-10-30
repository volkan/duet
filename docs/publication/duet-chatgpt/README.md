# Duet for ChatGPT publication materials

The Markdown files in this folder are the content sources for the Duet for
ChatGPT GitHub Pages site in `site/`. The site is published at the package URLs.

The publisher confirmed these values on 2026-10-26:

- Publisher name: Volkan Altan. The portal must verify the selected identity.
- Countries: all countries supported by OpenAI, encoded as `publication.countries: []`.
- Commerce: no payments or purchases, encoded as `review.commerce: false`.

The public package uses `https://volkan.github.io/duet/` for the site and its
support, privacy, and terms pages. All four pages are live and verified.

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

## Publication status

Review the published privacy, terms, and support pages before plugin submission.
Deployment uses GitHub Pages through `.github/workflows/pages.yml`.

Install and run the [ChatGPT acceptance task](../../CHATGPT_PLUGIN.md#validation)
against the packaged version. The portal's identity checks and policy
attestations must be completed by the authorized publisher. The plugin has not
been uploaded or submitted for review.
