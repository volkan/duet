# Releasing duet

duet ships from one repo three ways, all pinned to the same version string: the
**`duet-cli` PyPI package** and the **Claude Code** + **Codex** plugins (which
install from this GitHub repo). A release is a `chore: release X.Y.Z` commit that
bumps the three lockstep manifests, merged to `main`, then an annotated `vX.Y.Z`
tag. Pushing that tag fires `.github/workflows/release.yml`, which builds,
publishes to PyPI, and creates the GitHub Release.

## How publishing works

PyPI upload uses **Trusted Publishing (OpenID Connect)** — the
[PyPI](https://docs.pypi.org/trusted-publishers/)- and
[PyPA](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)-recommended
mechanism. There is **no PyPI secret stored in GitHub**: the `pypi-publish` job
presents a short-lived OIDC token that PyPI exchanges for a 15-minute upload
token. Do **not** add `PYPI_USERNAME`/`PYPI_PASSWORD` — account passwords are not
accepted for uploads, and Codespaces secrets are invisible to Actions anyway.

`release.yml` is intentionally **not** a required PR check: it triggers on tags,
not pull requests, so it never runs against a PR and is not part of branch
protection.

## The three lockstep version locations

The release bump edits exactly these (the `marketplace.json` files carry no
version). `scripts/check_distribution_metadata.py` fails if they disagree:

- `pyproject.toml` — `version = "..."`
- `.claude-plugin/plugin.json` — `"version": "..."`
- `plugins/duet/.codex-plugin/plugin.json` — `"version": "..."`

## One-time setup (required before the first tagged release)

These need PyPI / GitHub account access and are done once:

1. **PyPI trusted publisher.** On pypi.org → project **`duet-cli`** →
   *Manage → Publishing* (or <https://pypi.org/manage/account/publishing/>), add a
   **GitHub Actions** publisher with:
   - Owner: `volkan` · Repository: `duet` · Workflow filename: `release.yml` ·
     Environment: `pypi`

   The workflow filename and environment must match exactly; renaming either
   later breaks OIDC.

2. **GitHub Environment `pypi`** (repo *Settings → Environments → New
   environment* `pypi`):
   - **Required reviewers** (a maintainer) — the manual approval gate; the
     `pypi-publish` job pauses here until approved.
   - **Deployment branches and tags** → restrict to tags matching `v*`.

3. **Tag ruleset** (*Settings → Rules → Rulesets*) protecting `v*` — restrict who
   may create/delete release tags. PyPI's security notes recommend tag protection
   for tag-triggered publishing.

## Cutting a release

```sh
# 1. Land everything that should ship, with main green.
git switch main && git pull --ff-only
gh run list --branch main --limit 3        # confirm the latest main run is green

# 2. Bump the version on a release branch.
git switch -c chore/release-X.Y.Z
#    Edit the three lockstep manifests OLD -> X.Y.Z.
make ci && make package-check              # package-check builds + validates artifacts
git commit -am "chore: release X.Y.Z"      # body: what changed; semver rationale
git push -u origin chore/release-X.Y.Z
gh pr create --base main --title "chore: release X.Y.Z" --body "..."
#    Wait for the six required checks, then squash-merge.
gh pr merge <num> --squash --delete-branch
git switch main && git pull --ff-only && gh run list --branch main --limit 3

# 3. Release = push the annotated tag (one-time setup above must be done first).
git tag -a vX.Y.Z -m "duet X.Y.Z"
git push origin vX.Y.Z
```

Pushing the tag runs `release.yml`: `gate` (tag==version, tag-on-main, fast
`make ci`) → `build` (sdist+wheel, artifact/version validation) → **approve the
`pypi` environment in the GitHub UI** → `pypi-publish` → `github-release`.

## Verify

```sh
gh run watch                                  # the pypi-publish job waits on env approval
uvx --from duet-cli==X.Y.Z duet --help        # cold PyPI install
gh release view vX.Y.Z
git ls-remote --tags origin | grep vX.Y.Z
```

Plugins update in two independent ways (the plugins shell out to the
PATH-installed `duet`):

- **Plugin metadata/commands** (`/duet`, `$duet`, manifests): `/plugin marketplace
  update` (Claude Code) / `codex plugin marketplace update` (Codex).
- **The `duet` CLI runtime** users actually execute: `pipx upgrade duet-cli` (or a
  fresh `uvx --from duet-cli@latest …`). A marketplace update alone does **not**
  upgrade the binary.

## Manual fallback (only if OIDC is unavailable)

```sh
rm -rf dist && python -m build && python -m twine check dist/* && python -m twine upload dist/*
```

Authenticate with a **project-scoped API token** in `~/.pypirc` (username
`__token__`) — never the account password. PyPI versions are immutable: if an
upload half-fails, bump to the next patch and re-tag; never reuse a version.
