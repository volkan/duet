# Making CI a required merge gate

The workflow in `.github/workflows/ci.yml` runs on every pull request, but a
green/red check is only *advisory* until you mark it **required** in branch
protection. Once required, GitHub blocks the merge button while any required
check is failing or pending — while still letting a repo admin force-merge
(the explicit escape hatch).

## Day-to-day agent workflow

The default branch is `main`, not `master`. Agents and automation should never
push feature work directly to `main`; protected-branch failures are a signal to
open a PR, not a rule to bypass.

Use this flow before committing:

```sh
git fetch origin
git switch main
git pull --ff-only
git switch -c <type>/<short-topic>
```

If edits already exist on `main`, keep them and create a branch before
committing:

```sh
git switch -c <type>/<short-topic>
```

Before pushing, run `make ci`; for packaging or plugin changes also run
`make package-check` and `make plugin-check`. Then push the branch and open a
PR:

```sh
git push -u origin HEAD
gh pr create --base main --fill
```

Do not run `git push origin main`, `git push origin master`, or
`git push --force origin main` for normal work.

## Merge/release-to-main process

Every merge to `main` releases a new repository state. Use this checklist before
merging any PR:

1. Review the PR diff and confirm it contains only the intended scope.
2. Confirm the PR title or squash commit title follows Conventional Commits.
3. Confirm all six required checks are green:
   - `test (py3.9)`
   - `test (py3.11)`
   - `test (py3.13)`
   - `distribution metadata`
   - `plugin validate`
   - `complexity gate`
4. For packaging/plugin changes, confirm package and plugin validation ran
   (`make package-check` and `make plugin-check`, or their CI equivalents).
5. Merge through GitHub, preferably with squash merge and branch deletion:

   ```sh
   gh pr merge <number> --squash --delete-branch
   ```

6. After merging, sync local `main` and verify the post-merge CI run:

   ```sh
   git fetch origin
   git switch main
   git pull --ff-only
   gh run list --branch main --limit 3
   ```

Do not use an admin direct push as the normal merge path. If a post-merge
`main` run fails, fix it with another topic branch and PR.

## One-time setup (GitHub UI)

Settings → Branches → Branch protection rules → Add rule for `main`:

1. ✅ **Require status checks to pass before merging**
2. Add these checks (names come from the job `name:` fields in `ci.yml`):
   - `test (py3.9)`
   - `test (py3.11)`
   - `test (py3.13)`
   - `distribution metadata`
   - `plugin validate`
   - `complexity gate`
3. Leave **"Do not allow bypassing the above settings"** *unchecked* so
   administrators can still force-merge when they choose to.

## Or via `gh` (one command)

```sh
gh api -X PUT repos/:owner/:repo/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -F 'required_status_checks[strict]=true' \
  -f 'required_status_checks[checks][][context]=test (py3.9)' \
  -f 'required_status_checks[checks][][context]=test (py3.11)' \
  -f 'required_status_checks[checks][][context]=test (py3.13)' \
  -f 'required_status_checks[checks][][context]=distribution metadata' \
  -f 'required_status_checks[checks][][context]=plugin validate' \
  -f 'required_status_checks[checks][][context]=complexity gate' \
  -F 'allow_force_pushes=false' \
  -F 'allow_deletions=false' \
  -F 'enforce_admins=false' \
  -F 'required_pull_request_reviews=null' \
  -F 'restrictions=null'
```

`enforce_admins=false` is what preserves the admin force-merge path. The check
contexts must match the workflow's job `name:` values exactly — update both
together if you rename a job.

## Block force-pushes and branch deletion

The repository also needs an active branch ruleset targeting `refs/heads/main`
with these rules:

- `non_fast_forward` — blocks force-pushes to `main`
- `deletion` — blocks deleting `main`

Verify the live ruleset with:

```sh
gh api repos/:owner/:repo/rulesets/<ruleset-id>
```

The expected payload includes `"enforcement":"active"`,
`"include":["refs/heads/main"]`, and rules for both `deletion` and
`non_fast_forward`.

## What each check guards

| check | guards |
|---|---|
| `test (py3.x)` | unit tests, the reasoning-mapping check, and the dry-run smoke suite on the floor/mid/recent Python | 
| `distribution metadata` | `pyproject.toml`, plugin manifests, version lockstep, package build, wheel/sdist metadata, absolute README links, and installed-wheel smoke |
| `plugin validate` | `claude plugin validate .` against the Claude Code plugin manifest |
| `complexity gate` | no function over the cyclomatic-complexity (25) / length (160) budget — run locally with `make complexity` |

Run the fast local gate before pushing with `make ci`. For release or metadata
changes, also run `make package-check` and `make plugin-check`.
