# Making CI a required merge gate

The workflow in `.github/workflows/ci.yml` runs on every pull request, but a
green/red check is only *advisory* until you mark it **required** in branch
protection. Once required, GitHub blocks the merge button while any required
check is failing or pending — while still letting a repo admin force-merge
(the explicit escape hatch).

## One-time setup (GitHub UI)

Settings → Branches → Branch protection rules → Add rule for `main`:

1. ✅ **Require status checks to pass before merging**
2. Add these checks (names come from the job `name:` fields in `ci.yml`):
   - `test (py3.9)`
   - `test (py3.11)`
   - `test (py3.13)`
   - `complexity gate`
3. Leave **"Do not allow bypassing the above settings"** *unchecked* so
   administrators can still force-merge when they choose to.

## Or via `gh` (one command)

```sh
gh api -X PUT repos/:owner/:repo/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[checks][][context]=test (py3.9)' \
  -f 'required_status_checks[checks][][context]=test (py3.11)' \
  -f 'required_status_checks[checks][][context]=test (py3.13)' \
  -f 'required_status_checks[checks][][context]=complexity gate' \
  -F 'enforce_admins=false' \
  -F 'required_pull_request_reviews=null' \
  -F 'restrictions=null'
```

`enforce_admins=false` is what preserves the admin force-merge path. The check
contexts must match the workflow's job `name:` values exactly — update both
together if you rename a job.

## What each check guards

| check | guards |
|---|---|
| `test (py3.x)` | unit tests, the reasoning-mapping check, and the dry-run smoke suite on the floor/mid/recent Python | 
| `complexity gate` | no function over the cyclomatic-complexity (25) / length (160) budget — run locally with `make complexity` |

Run the whole gate locally before pushing with `make ci`.
