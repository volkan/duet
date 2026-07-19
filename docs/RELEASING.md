# Releasing duet

duet ships from one repo three ways, all pinned to the same runtime version: the
**`duet-cli` PyPI package** and the **Claude Code** + **Codex** plugins (which
install from this GitHub repo). A release is a `chore: release X.Y.Z` commit that
bumps the runtime source and two plugin manifests, merged to `main`. Merging it is the whole
trigger: `.github/workflows/release.yml` (`on: push: main`) detects the version
bump, builds, publishes to PyPI, then **auto-creates** the `vX.Y.Z` tag + GitHub
Release with the built distributions attached. There is no manual "create a
Release" step.

This supersedes the prior release-triggered design (where a human published a
GitHub Release to fire the workflow, and a bot was forbidden from creating one).
That "never bot-create the Release" rule was only a recursion-prevention artifact
of the release-triggered design — publishing no longer happens via a release
event, so a `GITHUB_TOKEN`-created Release here is fine and intentional.

## How publishing works

`release.yml` triggers `on: push: main` and runs a `detect` job first: it
releases only when `duet.__version__` **changed** in that push **and**
no `vX.Y.Z` tag exists yet. Routine pushes (docs, code) and the merge of this
rework itself are no-ops. When a push is release-worthy the jobs run in order:

1. `gate` — fast `make ci`.
2. `build` — sdist + wheel, then `check_distribution_metadata.py --artifacts dist`.
3. `pypi-publish` — uploads to PyPI; pauses on the protected `pypi` environment
   for **manual approval** first.
4. `create-release` — runs only **after** PyPI succeeds; auto-creates the
   `vX.Y.Z` tag + GitHub Release (`gh release create … --generate-notes`) with
   the built dists attached.

PyPI upload uses **Trusted Publishing (OpenID Connect)** — the
[PyPI](https://docs.pypi.org/trusted-publishers/)- and
[PyPA](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)-recommended
mechanism. There is **no PyPI secret stored in GitHub**: the `pypi-publish` job
presents a short-lived OIDC token that PyPI exchanges for a 15-minute upload
token. Do **not** add `PYPI_USERNAME`/`PYPI_PASSWORD` — account passwords are not
accepted for uploads, and Codespaces secrets are invisible to Actions anyway.
Because publishing happens in this same run (not via a release event), the
`GITHUB_TOKEN`-created Release in `create-release` does not need to fire any
other workflow, so no PAT/App token is required — the whole flow stays secretless
OIDC.

`release.yml` is intentionally **not** a required PR check: it triggers on pushes
to `main`, not on pull requests, so it never gates a PR and is not part of branch
protection.

## The canonical version and two lockstep manifests

The release bump edits exactly these (the `marketplace.json` files carry no
version). Setuptools derives wheel/sdist metadata from the first item, and
`scripts/check_distribution_metadata.py` fails if they disagree:

- `duet.py` — `__version__ = "..."` (canonical runtime/package source)
- `plugins/duet-claude/.claude-plugin/plugin.json` — `"version": "..."`
- `plugins/duet/.codex-plugin/plugin.json` — `"version": "..."`

`scripts/bump_release_version.py` (run by the **bump-version** workflow, below)
edits exactly these three and refuses a bad/duplicate/downgrade version.

## One-time setup (required before the first release)

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
   - **Deployment branches and tags** → allow `main` **and** `v*`. The publish
     run is on `refs/heads/main` (the push that merged the bump PR), not on a
     tag, so the branch must be allowed or the job can never start.

3. **Tag ruleset** (*Settings → Rules → Rulesets*) protecting `v*` — restrict who
   may delete release tags. It must **allow Actions to create tags** (have no
   `creation` rule), because `create-release` creates the `vX.Y.Z` tag itself.

## Cutting a release

```sh
# 1. Land everything that should ship, with main green.
git switch main && git pull --ff-only
gh run list --branch main --limit 3        # confirm the latest main run is green

# 2. Bump the version. Preferred: run the bump-version workflow
#    (GitHub -> Actions -> bump-version -> Run workflow -> enter X.Y.Z). It bumps
#    the runtime source and two plugin manifests and opens a release PR.
#    Local equivalent: python scripts/bump_release_version.py X.Y.Z
#    The bot-opened PR's required checks start in an approval-required state
#    (it was opened by GITHUB_TOKEN) — click "Approve workflows to run" in the
#    PR's merge box. Then review + squash-merge once the six checks pass:
gh pr merge <num> --squash --delete-branch
git switch main && git pull --ff-only && gh run list --branch main --limit 3

# 3. Merging the bump PR fires release.yml. Approve the `pypi` environment when
#    the run pauses (GitHub -> Actions -> the release run, or the email). The
#    tag vX.Y.Z and the GitHub Release are then created automatically, after
#    PyPI succeeds — there is nothing to type by hand.
```

Merging the bump PR fires `release.yml`: `detect` (version changed + no `vX.Y.Z`
tag) → `gate` (fast `make ci`) → `build` (sdist+wheel, artifact/version
validation) → **approve the `pypi` environment in the GitHub UI** →
`pypi-publish` → `create-release` (auto tag + Release with generated notes and
dists attached).

Recovery: the Release is created only **after** a successful PyPI publish, so a
failed or rejected run never leaves a published Release with nothing on PyPI. If a
run fails *after* PyPI already published, do **not** bump again or hand-create a
tag — use **"Re-run failed jobs"** on that same run. `skip-existing: true` makes
the PyPI step idempotent on the re-run, so typically only the `create-release`
(tag + Release) step needs to complete.

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
