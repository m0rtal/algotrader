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
   ```

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
