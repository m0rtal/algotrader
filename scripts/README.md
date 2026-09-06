# algotrader scripts

Helper scripts used by development tooling and CI.

## `scan-credentials.py`

Pre-commit credential scanner for staged git content. Detects:

- GitHub PAT (`ghp_*`, `gho_*`, `ghu_*`, `ghs_*`, `ghr_*`)
- GitLab PAT (`glpat-*`)
- AWS Access Key (`AKIA*`)
- Slack tokens (`xoxb-*`, `xoxp-*`, etc.)
- OpenAI API key (`sk-*`)
- Anthropic API key (`sk-ant-*`)
- Private keys (`-----BEGIN … KEY-----`)
- Generic high-entropy assignments to `token=`, `api_key=`, `secret=`, etc.

Runs automatically via `.husky/pre-commit` before every commit. Can also be
run manually:

```bash
python3 scripts/scan-credentials.py
```

Exit code: `0` = clean, `1` = secrets found (commit blocked).

Allowlist by file name (`ALLOWLIST_FILES`) or path pattern
(`ALLOWLIST_PATH_PATTERNS`) for known test fixtures and templates.

## `test_scanner_e2e.sh`

End-to-end smoke test that bypasses in-process string sanitization by building
a fake GitHub PAT via shell `printf` and verifying the scanner catches it.
Runs as part of `pnpm test:scripts`.

## `./refresh-codebase-memory.py`, `./update-adr.py`

Used by `.husky/{pre,post}-commit` hooks to keep the codebase-memory graph
and ADR documents in sync with code changes.
