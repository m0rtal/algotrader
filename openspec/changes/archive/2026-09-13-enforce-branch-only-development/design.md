# Design: enforce-branch-only-development

## Stack

- Shell script (`bash`) for the pre-push hook — stdlib only, no
  dependencies.
- GitHub Actions YAML for branch-name check — single job,
  `ubuntu-latest`, no setup-python.
- GitHub REST API for applying branch protection — applied once via
  `curl` + `GITHUB_TOKEN` from `/home/hermes/.hermes/.env`.
- Markdown for `CONTRIBUTING.md` and `AGENTS.md`.

## Layout

```
algotrader/
├── CONTRIBUTING.md                         (NEW)
├── AGENTS.md                               (NEW)
├── scripts/
│   ├── git-hooks/
│   │   └── pre-push                        (NEW, executable)
│   └── install-hooks.sh                    (NEW, executable)
└── .github/
    └── workflows/
        └── branch-name-check.yml           (NEW)
```

## Components

### 1. `scripts/git-hooks/pre-push`

Bash script. Reads lines from stdin (`<local_ref> <local_sha>
<remote_ref> <remote_sha>` per pushed ref). For each line, if
`remote_ref` is `refs/heads/main`, prints a 5-line error explaining
the rule and exits 1. Otherwise exits 0.

Example rejection message:

```
✗ Push to main is not allowed.

  Create a feature branch and open a PR:
    git checkout -b feature/<short-name>
    git push -u origin feature/<short-name>
    gh pr create --base main

  See CONTRIBUTING.md for the full workflow.
```

Exits 0 on first ref even if subsequent refs are bad (so a developer
pushing two branches at once doesn't get blocked for the second).
The check is per-ref.

### 2. `scripts/install-hooks.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
git config core.hooksPath scripts/git-hooks
chmod +x scripts/git-hooks/pre-push
echo "✓ Installed pre-push hook from scripts/git-hooks/"
```

Idempotent — running twice doesn't break anything.

### 3. `.github/workflows/branch-name-check.yml`

```yaml
name: branch-name-check
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - name: Validate branch name
        run: |
          BRANCH="${{ github.head_ref }}"
          if [[ ! "$BRANCH" =~ ^(feature|fix|chore|docs|hotfix)/[a-z0-9][a-z0-9-]*$ ]]; then
            echo "✗ Branch '$BRANCH' must match ^(feature|fix|chore|docs|hotfix)/<name>$"
            exit 1
          fi
```

No checkout needed. Branch name comes from `github.head_ref`.

### 4. GitHub branch protection rule (applied once)

Applied via:

```bash
curl -X PUT \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/m0rtal/algotrader/branches/main/protection \
  -d '{
    "required_status_checks": {"strict": true, "contexts": ["branch-name-check"]},
    "enforce_admins": true,
    "required_pull_request_reviews": {"required_approving_review_count": 1},
    "restrictions": null,
    "allow_force_pushes": false,
    "allow_deletions": false,
    "required_linear_history": false
  }'
```

This requires admin repo access. The token in
`/home/hermes/.hermes/.env` was confirmed to be admin-grade (can
push to `main`).

### 5. `CONTRIBUTING.md`

Plain markdown. Sections:

- Workflow overview (1 diagram: main ← PR ← feature/branch)
- Branch naming convention
- Commit message style (Conventional Commits — already enforced by
  pre-commit hook from previous work)
- PR checklist (CI green, at least 1 approval)
- After merge: delete branch, pull main

### 6. `AGENTS.md`

Plain markdown. Sections:

- Hard rule: never commit to `main`
- Branch creation: `feature/<name>` for new work, `fix/<name>` for
  bug fixes
- Per-task workflow: checkout main → pull → branch → commits →
  push -u → `gh pr create` → wait for review → merge → delete branch
- Forbidden: `git push -f main`, direct commits to main
- Trivial-fix exception: **none** — even one-line fixes get a PR

## Data flow

None. This change adds no runtime data. The branch protection rule
is metadata on the repo; the pre-push hook is a local script;
the GitHub Action is a workflow YAML; the docs are markdown.

## Testing

- **`tests/test_pre_push_hook.bash`**: shell test. Sources the hook
  via a fake git environment (`GIT_DIR`, `GIT_PUSH_OPTION_COUNT`,
  and stdin). Verifies:
  - push to `main` → exit 1, error message printed
  - push to `feature/x` → exit 0, no output
  - push to multiple refs (main + feature) → exit 0 (the first
    invalid ref doesn't block the rest)
- **Manual verification**: apply the rule, attempt a force-push
  to `main`, observe 422 from GitHub.
- **Live check**: `curl .../branches/main/protection` returns the
  expected JSON after apply.

## Rollback

If the rule is too restrictive, the operator can:

- Disable the local hook: `git config --unset core.hooksPath`
- Disable branch protection via GitHub UI or
  `DELETE .../branches/main/protection`
- `CONTRIBUTING.md` and `AGENTS.md` can be deleted

The rule is reversible in two commands.

## Operator notes

- The rule takes effect immediately after the PR is merged.
- The first push to a feature branch needs `-u` to set upstream.
- Branch protection only applies to `main`. Long-running feature
  branches don't have it.
- If the AI agent pushes to `main` in a future session, the
  pre-push hook blocks it locally before it reaches GitHub.
  The hook's error message references `CONTRIBUTING.md`.
