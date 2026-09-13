# dev-workflow Specification

## Purpose

The dev-workflow capability governs how changes land in the algotrader repository. It defines the branch-only development rule, the naming convention, and the enforcement layers (local pre-push hook, GitHub branch protection, GitHub Actions workflow, and AGENTS.md convention for AI agents). Every change to `main` must come from a merged pull request originating on a `feature|fix|chore|docs|hotfix/<name>` branch.

## Requirements

### Requirement: All changes land in feature/fix branches and reach main via merged PR

The system SHALL require that every commit to the `main` branch
originates from a merged pull request whose source branch matches
the pattern `^(feature|fix|chore|docs|hotfix)/[a-z0-9][a-z0-9-]*$`.
Direct commits to `main` and direct pushes to `main` are prohibited
at three layers:

1. A local pre-push hook (`scripts/git-hooks/pre-push`) blocks
   `git push` to `refs/heads/main` from the local clone and prints
   an error message pointing to the branch-creation workflow.
2. A GitHub branch protection rule on `main` rejects any push that
   bypasses the local hook, with `required_status_checks.strict:
true` for the `branch-name-check` workflow,
   `required_pull_request_reviews.required_approving_review_count:
1`, `enforce_admins: true`, `allow_force_pushes: false`, and
   `allow_deletions: false`.
3. A convention documented in `CONTRIBUTING.md` and `AGENTS.md`
   that all contributors (human and AI) follow on day one.

#### Scenario: developer pushes a feature branch

- GIVEN the operator is on `feature/add-xyz`
- WHEN they run `git push -u origin feature/add-xyz`
- THEN the local hook exits 0 (the destination is not `main`)
- AND the GitHub branch-name-check workflow passes
- AND the PR is mergeable after 1 approval

#### Scenario: developer tries to push directly to main

- GIVEN the operator is on `main`
- WHEN they run `git push origin main`
- THEN the local hook exits 1 and prints an error explaining the
  branch-only rule
- AND no commit reaches the remote

#### Scenario: AI agent force-pushes main despite the hook

- GIVEN the AI agent bypasses the local hook with `git push
--no-verify`
- WHEN the push request reaches the GitHub API
- THEN GitHub returns 422 with a "branch is protected" error
- AND no commit lands on `main`

#### Scenario: PR comes from a branch with a non-conforming name

- GIVEN a contributor opens a PR from `experiment/new-thing`
- WHEN the `branch-name-check` workflow runs
- THEN the workflow fails because `experiment/...` is not in the
  allowed prefix list
- AND the PR cannot be merged

### Requirement: AI agents follow the branch workflow on every commit

The `AGENTS.md` file in the repo root SHALL document the AI agent's
mandatory workflow:

1. `git checkout main && git pull --rebase`
2. `git checkout -b feature/<name>` (or `fix/<name>`, `chore/<name>`,
   etc.)
3. ... commits ...
4. `git push -u origin <branch>`
5. `gh pr create --base main`
6. Wait for review and CI green
7. Merge via `gh pr merge --merge --delete-branch`

The workflow SHALL apply to every change regardless of size. There
are no exceptions for trivial fixes, typos, or one-line coverage
pragmas. Force-push to `main` is explicitly prohibited in the
documented workflow.

#### Scenario: agent commits a one-line typo fix

- GIVEN the operator asks the AI agent to fix a typo in
  `CONTRIBUTING.md`
- WHEN the agent commits the fix
- THEN the agent first creates `fix/typo-in-contributing`
- AND opens a PR
- AND does not commit directly to `main`

### Requirement: Branch naming follows a prefix convention

Every source branch for a PR to `main` SHALL match the regular
expression `^(feature|fix|chore|docs|hotfix)/[a-z0-9][a-z0-9-]*$`.
The `branch-name-check` GitHub Actions workflow SHALL enforce this
on every PR open / synchronize / reopen event.

#### Scenario: PR from a conforming branch

- GIVEN a PR from `feature/data-completeness-backfill`
- WHEN the `branch-name-check` workflow runs
- THEN the regex matches and the check passes

#### Scenario: PR from a non-conforming branch

- GIVEN a PR from `my-branch` or `bugfix/thing` or `Feature/X`
- WHEN the `branch-name-check` workflow runs
- THEN the check fails
- AND the PR is blocked from merge until the branch is renamed
