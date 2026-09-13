# enforce-branch-only-development

## Why

Today every change to the algotrader repository — even a single-character
typo fix — is committed directly to `main` and force-pushed via the
AI agent's convenience command. `main` has no production-equivalent
guarantees because there's no review checkpoint between a commit and
its landing. The same agent that has been doing the work also approves
the work. That's not a separation-of-concerns problem we can solve by
asking the agent to be more careful: it's a structural gap.

The pre-change state is "main is whatever the last commit happened to
be." The post-change state is "main is whatever passed review after a
PR was opened." Trivial typos still get reviewed — that's the cost we
chose. Slow but safe.

The change introduces a hard rule: every change lands in a
`feature/...` or `fix/...` branch first; `main` is updated only via
a merged PR. The rule is enforced at three layers:

1. **Local pre-push hook** (`scripts/git-hooks/pre-push`) — blocks
   `git push` to `main` from the local clone, regardless of whether
   it's a fast-forward or a force-push. Prints the exact branch name
   and how to install it.
2. **GitHub branch protection** on `main` — the server-side gate
   that refuses the push if anyone (or any agent) finds a way to
   bypass the local hook. Requires a PR before merge.
3. **Convention** (`CONTRIBUTING.md`) — documents the rule so a new
   contributor knows the workflow on day one.

The AI agent (this assistant) switches from `git push -f main` to
`gh pr create` for every change, including one-line fixes. The
previous behavior is explicitly documented as disallowed.

## What Changes

1. **`CONTRIBUTING.md`** — new file. Documents the branch-only rule,
   PR workflow, branch naming conventions, and the agent's role.

2. **`scripts/git-hooks/pre-push`** — new executable script. Reads
   the `remote` and `local ref` from the hook's stdin. If the
   destination is `main` (or `refs/heads/main`), prints an error
   message and exits non-zero. Otherwise exits zero. The error
   message tells the user how to make a branch.

3. **`scripts/install-hooks.sh`** — new executable script. Sets
   `core.hooksPath` to `scripts/git-hooks/` for the repo so the
   hook is active immediately. Idempotent.

4. **`.github/workflows/branch-name-check.yml`** — new workflow. On
   PR open / sync, checks that the source branch matches
   `^(feature|fix|chore|docs|hotfix)/[a-z0-9][a-z0-9-]*$`. PRs from
   `main` or arbitrary branches fail the check.

5. **GitHub branch protection rule** on `main` (applied via
   `gh api` during implementation, not stored in this repo):
   - `required_status_checks: { strict: true, contexts: ["branch-name-check"] }`
   - `enforce_admins: true`
   - `required_pull_request_reviews: { required_approving_review_count: 1 }`
   - `restrictions: null` (anyone can push to feature branches)
   - `allow_force_pushes: false`
   - `allow_deletions: false`

6. **`AGENTS.md`** — new file. Documents the AI agent's branch
   workflow in detail: never commit to `main`; create
   `feature/<name>` or `fix/<name>`; commit; `git push -u`; `gh pr
create`; wait for review; merge. Force-push to `main` is
   explicitly prohibited regardless of change size.

## Impact

- **AI agent workflow**: every future change requires a branch +
  PR cycle. Estimated cost: ~30s of overhead per change for branch
  creation, push, PR creation, merge. For trivial changes this is
  the main friction.
- **Operator workflow**: when the agent hands off a PR, the operator
  reviews it, merges it via `gh` or the GitHub UI, then the agent
  deletes the branch and continues on `main`.
- **CI**: a new GitHub Actions job runs on every PR. It only
  checks the branch name pattern; it doesn't run the test suite
  (that's a separate, larger change).
- **Cron guardian / data-completeness work**: unaffected. These
  run on `main` (already merged) and don't push to remote during
  their work.

## Non-Goals

- **No auto-merge** even when CI passes. The operator must always
  click merge.
- **No CODEOWNERS file** in this change. Single-maintainer repo
  today.
- **No squash-vs-merge strategy** decision. Default GitHub merge
  commit is fine for now.
- **No release branches.** Single-branch release model.
- **No branch protection on feature branches.** Only `main`.
- **No pre-commit hook** for test runs. Pre-commit hooks that run
  tests block AI agent dispatch loops; that's a separate concern.

## Risks

- **First-time setup cost**. Branch protection must be applied via
  GitHub API or UI. If the API call fails, the rule isn't in place
  and the agent can still push. Mitigation: the local pre-push hook
  is the primary defense; the branch protection is defense in depth.
  Verification step (live check via `gh api .../protection`) is
  part of the implementation.
- **Operator friction**. PR review adds ~30s to ~2min per change.
  User explicitly accepted this trade-off ("Slow but safe").
- **Local hook bypassable**. A developer can run `git push --no-verify`
  or temporarily disable `core.hooksPath`. Mitigation: this is the
  same trust model as any git hook. The hook's job is to remind,
  not to enforce.
- **AI agent memory drift**. The agent's persistent memory will
  mention the new rule, but old behaviour may persist for a while.
  Mitigation: `AGENTS.md` is part of the repo and read by the
  agent on startup; this change updates the agent's memory entry
  to point at it.
