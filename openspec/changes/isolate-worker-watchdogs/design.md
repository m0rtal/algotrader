# Design

## Source evidence
Base: 5aee46b824c67dd8875d4fc9ed43d515d4c25830. Supervisor lines 64/153 use `${STATE_DB}.worker.pid`; lines 96-99 select a global heartbeat. Worker heartbeat_loop writes exact `detail=pid=<os.getpid()>` in pipeline; live heartbeat writes worker_pid in pipeline_heartbeat. Startup lines 101-103 combine path and name. The clean wrapper already unsets PYTHONPATH/PYTHONHOME and launches explicit daily first. Startup tests line10 hardcode production path.

## Boundaries
Keep cwd-derived STATE_DB, service identities, default 30-second polling, 5-second restart delay and existing heartbeat-age constant 0.0208 days. Main loop and watchdog use one `${STATE_DB}.${NAME}.worker.pid` derivation. Reject unsafe service filename components, without changing valid names.

Use a small stdlib-only Python helper where needed to make the load-bearing query and process-control path executable under isolated tests. Read SQLite via mode=ro and query_only; never create a missing database. Scope pipeline rows by exact `phase=worker.heartbeat` and exact `detail=pid=<child>`; scope pipeline_heartbeat by worker_pid. Parse naive stored heartbeat times as UTC. Ignore heartbeat rows older than the child's process start (floor to seconds to match daily heartbeat precision). Missing own heartbeat gets grace equal to the existing heartbeat-age threshold from actual child start; sibling rows do not supply grace/freshness. Future/malformed timestamps, unavailable DB/process identity and unsupported pidfd are unknown and cause no signal.

Before a stale-child signal: verify numeric PID-file content, expected supervisor PPID, process start identity and unchanged PID-file target. Open Linux pidfd, revalidate identity/target, and send SIGKILL through that descriptor only. Close descriptors on all paths. Do not fall back to unsafe numeric kill. Emit bounded outcome/error-class diagnostics, never raw exception text, credentials or DB payloads. A logging override for disposable tests may retain the existing production default.

Boot first via the existing clean wrapper with no extra arguments. Boot derived with PYTHONPATH/PYTHONHOME unset; retain its exact daily derived role and API cwd. Preserve API-health-before-workers ordering and refusal when health fails.

## Tests and rollout risks
Tests own all DBs, logs, match fixtures and process groups. Never execute production cron/recovery launchers. Use real temporary SQLite and owned subprocesses for identity/pidfd behavior; finite waits and finally cleanup are mandatory. Source-only assertions cannot substitute for stale-own/fresh-sibling behavior.

A source update does not update running Bash supervisors. Parent must plan activation against exact supervisor/watchdog/worker identities after review; no implementation child may operate production. Existing shared-file supervisors must not be left able to signal a migrated sibling. Seven-day observation begins only after the final operational transition and all separate readiness gates pass.
