# Agent workflow

This repository's default branch is `main`, not `master`. Agents and automation
must not commit or push feature work directly to `main`; protected-branch
failures are a signal to open a PR, not a rule to bypass.

## Daily branch workflow

Start from an up-to-date `main`, create a topic branch, and commit there:

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

Use Conventional Commits. Before pushing, run the relevant local gates:

```sh
make ci
# For package/plugin metadata changes:
make package-check
make plugin-check
```

Then push the branch and open a PR against `main`:

```sh
git push -u origin HEAD
gh pr create --base main --fill
```

Do not run `git push origin main`, `git push origin master`, or
`git push --force origin main` for normal work.

## Merge/release-to-main checklist

Every merge to `main` releases a new repository state. Merge through GitHub; do
not land work by pushing directly to `main`.

Before merging:

1. Confirm the PR diff matches the requested scope and does not include local
   scratch files or unrelated generated artifacts.
2. Confirm the PR title or squash commit title is a Conventional Commit.
3. Confirm all six required checks are passing:
   `test (py3.9)`, `test (py3.11)`, `test (py3.13)`,
   `distribution metadata`, `plugin validate`, and `complexity gate`.
4. For packaging/plugin changes, confirm `make package-check` and
   `make plugin-check` were run locally or passed in CI. For PyPI releases,
   stop before upload unless the owner explicitly approves publishing.

Useful commands:

```sh
gh pr view <number> --json title,headRefName,baseRefName,mergeStateStatus
gh pr checks <number>
gh pr diff <number> --name-only
gh pr merge <number> --squash --delete-branch
```

After merging:

```sh
git fetch origin
git switch main
git pull --ff-only
gh run list --branch main --limit 3
```

Verify the latest `main` run is green. If it is not, treat the failed run as the
next task and fix it through another topic branch + PR.
