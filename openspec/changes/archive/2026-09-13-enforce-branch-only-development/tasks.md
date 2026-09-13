# Tasks: enforce-branch-only-development

## 1. CONTRIBUTING.md and AGENTS.md

- [ ] 1.1 Write `CONTRIBUTING.md` with branch-only rule, PR workflow,
      naming convention, PR checklist
- [ ] 1.2 Write `AGENTS.md` with the AI agent's branch workflow
- [ ] 1.3 Verify both files render correctly in GitHub UI

## 2. Pre-push hook + installer

- [ ] 2.1 Write `scripts/git-hooks/pre-push` (bash, exits 1 on push
      to main with error message)
- [ ] 2.2 Write `scripts/install-hooks.sh` (sets `core.hooksPath`)
- [ ] 2.3 Tests: `tests/test_pre_push_hook.bash` covers 3 cases
      (push to main, push to feature, multi-ref)
- [ ] 2.4 Commit hook + installer

## 3. GitHub Actions workflow

- [ ] 3.1 Write `.github/workflows/branch-name-check.yml` with
      branch name regex check
- [ ] 3.2 Verify workflow file is valid YAML (python -c "import yaml")
- [ ] 3.3 Commit workflow

## 4. Apply GitHub branch protection (live operator action)

- [ ] 4.1 Verify GITHUB_TOKEN has admin access to m0rtal/algotrader
- [ ] 4.2 PUT `https://api.github.com/repos/m0rtal/algotrader/branches/main/protection`
      with the JSON body from design.md §4
- [ ] 4.3 GET `.../protection` to confirm rule is active
- [ ] 4.4 Attempt a force-push to `main` to confirm 422 response

## 5. Spec apply + archive

- [ ] 5.1 `openspec validate enforce-branch-only-development --strict`
- [ ] 5.2 Apply delta to canonical `openspec/specs/dev-workflow/spec.md`
- [ ] 5.3 `openspec validate dev-workflow --strict`
- [ ] 5.4 `openspec archive enforce-branch-only-development --yes --skip-specs`

## 6. PR creation + merge

- [ ] 6.1 Commit all spec + impl changes
- [ ] 6.2 `git push -u origin feature/enforce-branch-only-development`
- [ ] 6.3 `gh pr create --base main --title "feat: enforce branch-only development" --body "..."`
- [ ] 6.4 Wait for branch-name-check to pass
- [ ] 6.5 Merge PR via `gh pr merge --merge --delete-branch`
- [ ] 6.6 `git checkout main && git pull`
- [ ] 6.7 Install hook locally: `bash scripts/install-hooks.sh`
- [ ] 6.8 Verify hook works: `git push` to main exits with error

## 7. Live verification

- [ ] 7.1 From a fresh feature branch, make a trivial change,
      push it, observe GitHub accepts the push
- [ ] 7.2 Try to `git push -f origin main` from main, observe the
      local hook blocks it
- [ ] 7.3 Confirm GitHub branch protection rejects any direct push
      attempt (server-side test if hook is bypassed)
