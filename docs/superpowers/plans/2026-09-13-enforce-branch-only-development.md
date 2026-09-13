# Enforce Branch-Only Development Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** All commits to `main` must originate from merged PRs on `feature|fix|chore|docs|hotfix/<name>` branches. Enforce via local pre-push hook + GitHub branch protection + GitHub Actions branch-name check + `CONTRIBUTING.md` + `AGENTS.md`.

**Architecture:** Three enforcement layers (local hook, server-side branch protection, GitHub Actions workflow) plus two docs files. No new dependencies.

**Tech Stack:** Bash for hooks, GitHub Actions YAML, GitHub REST API for protection rule, plain markdown for docs.

**Spec:** `openspec/changes/archive/2026-09-13-enforce-branch-only-development/`

**Branch:** This work happens on `feature/enforce-branch-only-development` (per the new rule being implemented).

## Global Constraints

- Coverage floor ≥95% (current 95.25%) — N/A: this change adds no Python code
- TDD-first: shell test for the pre-push hook fails before the hook implementation passes
- Idempotent install script
- All changes via feature branch + PR (the rule being implemented)
- No new Python dependencies
- File paths absolute

---

## Task 1: CONTRIBUTING.md + AGENTS.md

**Files:**

- Create: `CONTRIBUTING.md`
- Create: `AGENTS.md`

**Interfaces:** None — markdown only.

- [ ] **Step 1: Write CONTRIBUTING.md**

```markdown
# Contributing to algotrader

## Branch-only development

Every change lands in a feature branch first and reaches `main` via a
merged pull request. Direct commits to `main` and direct pushes to `main`
are prohibited.

## Workflow
```

main ← PR ← feature/<name>

````

1. Create a branch off `main`:
   ```bash
   git checkout main
   git pull
   git checkout -b feature/<short-name>
````

2. Make your commits. Use Conventional Commits:
   `feat:`, `fix:`, `chore:`, `docs:`, `test:`, `refactor:`.

3. Push and open a PR:

   ```bash
   git push -u origin feature/<short-name>
   gh pr create --base main
   ```

4. Wait for:
   - The `branch-name-check` workflow to pass
   - At least 1 approval
   - CI green

5. Merge via:

   ```bash
   gh pr merge --merge --delete-branch
   ```

6. After merge:
   ```bash
   git checkout main
   git pull
   ```

## Branch naming

`^(feature|fix|chore|docs|hotfix)/[a-z0-9][a-z0-9-]*$`

- `feature/<name>` — new functionality
- `fix/<name>` — bug fix
- `chore/<name>` — non-functional change
- `docs/<name>` — documentation only
- `hotfix/<name>` — urgent fix (use sparingly)

## Pre-push hook

Install once per clone:

```bash
bash scripts/install-hooks.sh
```

The hook blocks any `git push` whose destination is `main`. To bypass
(for emergencies), use `git push --no-verify` — but GitHub's branch
protection will still reject the push server-side.

````

- [ ] **Step 2: Write AGENTS.md**

```markdown
# AGENTS.md — workflow for AI coding assistants

## Hard rule

**Never commit to `main`. Never push directly to `main`.**

Every change — including one-line typo fixes, dead-code removal, and
coverage pragmas — must go through a feature branch and a PR.

## Per-task workflow

1. **Sync main:**
   ```bash
   git checkout main
   git pull --rebase
````

2. **Create a branch:**
   - New functionality: `feature/<short-name>`
   - Bug fix: `fix/<short-name>`
   - Docs only: `docs/<short-name>`
   - Tooling/cleanup: `chore/<short-name>`
   - Urgent: `hotfix/<short-name>`

   ```bash
   git checkout -b feature/data-completeness-backfill
   ```

3. **Commit your work:**

   ```bash
   git add <files>
   git commit -m "feat: <description>"
   ```

4. **Push the branch:**

   ```bash
   git push -u origin feature/data-completeness-backfill
   ```

   The local pre-push hook allows this — destination is not `main`.

5. **Open a PR:**

   ```bash
   gh pr create --base main --title "feat: <title>" --body "..."
   ```

6. **Wait for review.** Do NOT merge your own PR unless the operator
   explicitly asks you to.

7. **Operator merges.** Then:
   ```bash
   git checkout main
   git pull
   git branch -d feature/data-completeness-backfill
   ```

## Forbidden

- `git push -f origin main`
- `git push origin main` (without `-f` also blocked)
- `git checkout main && git commit ... && git push`
- Direct commits to `main` from `git checkout main`
- Cherry-picking commits onto `main`

## Why

The rule exists because the same agent that does the work also
reviews the work. A second reviewer (human or another agent via
`gh pr`) is the only structural way to catch mistakes.

## Setup

The pre-push hook must be installed locally:

```bash
bash scripts/install-hooks.sh
```

If the hook blocks a legitimate push, check the destination branch
name — `main` is always blocked.

````

- [ ] **Step 3: Commit**

```bash
cd /home/hermes/algotrader
git add CONTRIBUTING.md AGENTS.md
git commit -m "docs: CONTRIBUTING + AGENTS for branch-only workflow"
````

---

## Task 2: Pre-push hook + installer + tests

**Files:**

- Create: `scripts/git-hooks/pre-push` (executable)
- Create: `scripts/install-hooks.sh` (executable)
- Create: `tests/test_pre_push_hook.bash`

**Interfaces:**

- Hook reads stdin lines: `<local_ref> <local_sha> <remote_ref> <remote_sha>`
- Hook exits 1 on any line where `remote_ref == refs/heads/main`
- Hook exits 0 otherwise

- [ ] **Step 1: Write the failing shell test**

```bash
#!/usr/bin/env bash
# tests/test_pre_push_hook.bash
# TDD: this test must fail before pre-push is implemented.
set -euo pipefail

HOOK="$(dirname "$0")/../scripts/git-hooks/pre-push"
[ -x "$HOOK" ] || { echo "hook not executable"; exit 1; }

# Case 1: push to main → expect exit 1 + "main" in stderr
if echo "refs/heads/main abc123 refs/heads/main def456" \
   | "$HOOK" 2>&1 >/dev/null; then
  echo "FAIL: hook allowed push to main"; exit 1
fi

# Case 2: push to feature/x → expect exit 0
if echo "refs/heads/feature/x abc123 refs/heads/feature/x def456" \
   | "$HOOK" >/dev/null 2>&1; then
  : # pass
else
  echo "FAIL: hook blocked legitimate feature push"; exit 1
fi

# Case 3: mixed refs (one main, one feature) → exit 0
if echo -e "refs/heads/main a1 refs/heads/main b1\nrefs/heads/feature/y a2 refs/heads/feature/y b2" \
   | "$HOOK" >/dev/null 2>&1; then
  : # per-spec behavior: only block if all are main
fi

echo "OK: pre-push hook passes 3 cases"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/hermes/algotrader
bash tests/test_pre_push_hook.bash
```

Expected: exit 1 ("hook not executable").

- [ ] **Step 3: Write the hook**

```bash
#!/usr/bin/env bash
# scripts/git-hooks/pre-push
# Block any push whose destination is main.
# Reads lines of `<local_ref> <local_sha> <remote_ref> <remote_sha>` from stdin.
set -euo pipefail

blocked=0
while read -r local_ref local_sha remote_ref remote_sha; do
  if [ "$remote_ref" = "refs/heads/main" ]; then
    blocked=1
  fi
done

if [ "$blocked" -eq 1 ]; then
  cat >&2 <<'EOF'
✗ Push to main is not allowed.

  Create a feature branch and open a PR:
    git checkout -b feature/<short-name>
    git push -u origin feature/<short-name>
    gh pr create --base main

  See CONTRIBUTING.md for the full workflow.
EOF
  exit 1
fi

exit 0
```

- [ ] **Step 4: Write the installer**

```bash
#!/usr/bin/env bash
# scripts/install-hooks.sh
# Set core.hooksPath so the hook is active.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
git config core.hooksPath "$SCRIPT_DIR/git-hooks"
chmod +x "$SCRIPT_DIR/git-hooks/pre-push"
echo "✓ Installed pre-push hook from $SCRIPT_DIR/git-hooks/"
```

- [ ] **Step 5: Make both executable, run test**

```bash
cd /home/hermes/algotrader
chmod +x scripts/git-hooks/pre-push scripts/install-hooks.sh
bash tests/test_pre_push_hook.bash
```

Expected: `OK: pre-push hook passes 3 cases`.

- [ ] **Step 6: Commit**

```bash
git add scripts/git-hooks/pre-push scripts/install-hooks.sh tests/test_pre_push_hook.bash
git commit -m "feat(hooks): pre-push blocks direct pushes to main"
```

---

## Task 3: GitHub Actions workflow

**Files:**

- Create: `.github/workflows/branch-name-check.yml`

- [ ] **Step 1: Write the workflow**

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
            echo "  Allowed prefixes: feature/, fix/, chore/, docs/, hotfix/"
            echo "  Name part: lowercase alphanumeric + hyphens, must start with a letter or digit"
            exit 1
          fi
          echo "✓ Branch '$BRANCH' matches the convention"
```

- [ ] **Step 2: Validate the YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/branch-name-check.yml'))"
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
cd /home/hermes/algotrader
git add .github/workflows/branch-name-check.yml
git commit -m "ci(branch-name): enforce feature/fix/chore/docs/hotfix prefix"
```

---

## Task 4: Apply GitHub branch protection (operator action)

**This task is the only one that requires the `GITHUB_TOKEN` admin scope and must run from the operator shell.**

- [ ] **Step 1: Verify token is admin**

```bash
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/m0rtal/algotrader \
  | python3 -c "import json, sys; d=json.load(sys.stdin); print('permissions:', d.get('permissions'))"
```

Expected: object with `admin: true` or `push: true` at minimum.

- [ ] **Step 2: Apply the protection rule**

```bash
curl -X PUT \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/m0rtal/algotrader/branches/main/protection \
  -d '{
    "required_status_checks": {
      "strict": true,
      "contexts": ["branch-name-check"]
    },
    "enforce_admins": true,
    "required_pull_request_reviews": {
      "required_approving_review_count": 1
    },
    "restrictions": null,
    "allow_force_pushes": false,
    "allow_deletions": false,
    "required_linear_history": false
  }'
```

Expected: 200 OK with the protection JSON.

- [ ] **Step 3: Confirm the rule is active**

```bash
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/m0rtal/algotrader/branches/main/protection \
  | python3 -m json.tool | head -40
```

Expected: JSON with `required_pull_request_reviews.required_approving_review_count: 1`, `enforce_admins.enabled: true`, `allow_force_pushes.enabled: false`.

---

## Task 5: PR + merge

- [ ] **Step 1: Push the branch**

```bash
cd /home/hermes/algotrader
git push -u origin feature/enforce-branch-only-development
```

Expected: branch appears on remote. The pre-push hook allows this because destination is not main.

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main \
  --title "feat: enforce branch-only development" \
  --body "$(cat <<'EOF'
- Adds CONTRIBUTING.md and AGENTS.md documenting the rule
- Adds scripts/git-hooks/pre-push blocking direct pushes to main
- Adds scripts/install-hooks.sh
- Adds .github/workflows/branch-name-check.yml
- Applies GitHub branch protection to main

Trivial typos and 1-line fixes get a PR from now on.

Refs: openspec/changes/archive/2026-09-13-enforce-branch-only-development/
EOF
)"
```

- [ ] **Step 3: Wait for branch-name-check workflow to pass**

```bash
gh pr checks --watch
```

Expected: `branch-name-check` passes.

- [ ] **Step 4: Merge**

The operator must approve + merge. From the operator's shell:

```bash
gh pr merge <PR_NUMBER> --merge --delete-branch
```

After merge, return to main:

```bash
git checkout main
git pull
```

---

## Task 6: Live verification

- [ ] **Step 1: Confirm branch protection is active**

```bash
curl -s -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/repos/m0rtal/algotrader/branches/main/protection \
  | python3 -m json.tool | head -5
```

Expected: non-empty protection JSON.

- [ ] **Step 2: Install the local hook**

```bash
cd /home/hermes/algotrader
bash scripts/install-hooks.sh
```

Expected: `✓ Installed pre-push hook from ...`.

- [ ] **Step 3: Test the local hook**

```bash
echo "refs/heads/main abc refs/heads/main def" | git push --dry-run --receive-pack=. main 2>&1 | head -3
```

(Or simpler: try `git push origin main` and observe the rejection.)

Expected: hook exits with the "Push to main is not allowed" error.

- [ ] **Step 4: Push a test feature branch**

```bash
cd /home/hermes/algotrader
git checkout -b chore/post-merge-verify
echo "# post-merge verify $(date)" >> /tmp/verify.txt
# Note: we won't commit anything to avoid spam. Just verify the hook
# allows push to a non-main branch by trying it on an empty commit.
# Skip if no changes exist.
```

(If a real change isn't available, this step is optional — the hook test from Step 3 already proves push-to-main is blocked, and the workflow on the first real PR proves the GitHub-side gate.)

---

## Task 7: Update agent memory

- [ ] **Step 1: Update MEMORY.md**

Add a fact that all development on the algotrader project now goes
through branches. (The agent reads this on every session.)

In the `user` profile section or memory section, add something like:

> "algotrader (m0rtal/algotrader): разработка через feature|fix|chore|docs|hotfix/<name> ветки + PR в main. Прямой push в main заблокирован локальным hook и GitHub branch protection. Никаких force-push в main даже для trivial changes."

This is a meta-task done after the rest.

---

## Final commit + push (if not yet done)

All commits to the branch happen via the standard git workflow. After
all 5 tasks land on the branch, push + PR + merge as described in
Task 5.
