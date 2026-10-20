---
paths:
  - "scraper/scripts/**/*"
  - "scraper/deploy/**/*"
  - "scraper/Dockerfile"
  - "scraper/.gitattributes"
---

# Daily runners and deployment

These files run unattended every morning. A mistake here fails silently until someone notices
missing prices, so change them conservatively.

## Invariants of every daily runner

- Exactly one run at a time: `flock` in `run_daily.sh`, a named mutex in `run_daily.ps1`,
  `MultipleInstances IgnoreNew` in the scheduled task. A concurrent run exits `0` and logs that it skipped.
- Propagate the CLI exit code unchanged (`0` ok, `1` partial, `2` failed). systemd treats `1` as
  success through `SuccessExitStatus=1`; keep that consistent if codes change.
- Append to `logs/scraper-YYYY-MM-DD.log` and prune logs older than 30 days.
- Run from the project directory with `uv run --frozen`, so the lock file is respected.
- Never add `--details`, a hard-coded database URL or credentials to a runner; configuration comes
  from `.env` or the environment.

## Platform specifics

- PowerShell scripts must work on Windows PowerShell 5.1 (`#Requires -Version 5.1`): no `&&`, `||`,
  ternaries or other PowerShell 7-only syntax. Set `$ErrorActionPreference = "Stop"` and `PYTHONUTF8=1`.
- Shell scripts use `#!/usr/bin/env bash` and `set -euo pipefail`, and must keep LF endings
  (enforced by `.gitattributes`; `.ps1` is CRLF).
- systemd units keep their sandboxing (`ProtectSystem=strict`, `ReadWritePaths`, `NoNewPrivileges`).
  If the app needs a new writable path, add it to `ReadWritePaths` instead of removing protections.
- The Docker image runs as the non-root `scraper` user with `/data` as a volume and installs with
  `uv sync --frozen --no-dev`.

## Scheduling

- Default time is 06:30 Europe/Madrid, after Mercadona's overnight price update, with jitter on systemd.
- Keep catch-up behaviour: `Persistent=true` (systemd), `StartWhenAvailable` (Task Scheduler).
- When you change a schedule or a runner, update README "Daily execution".
