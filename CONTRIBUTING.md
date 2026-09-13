# Contributing to algotrader

## Branch-only development

Every change lands in a feature branch first and reaches `main` via a
merged pull request. Direct commits to `main` and direct pushes to `main`
are prohibited.

## Workflow

```
main ← PR ← feature/<name>
```

1. Create a branch off `main`:

   ```bash
   git checkout main
   git pull
   git checkout -b feature/<short-name>
   ```

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
