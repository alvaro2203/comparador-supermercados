---
name: diagnose-scrape-run
description: Investigate a daily scraper run that ended partial or failed (exit code 1 or 2), find the root cause from run history and logs, and fix it, typically supermarket API drift reported as SourceSchemaError. Use when the user says the scraper failed, prices are missing for a day, or a run was partial.
argument-hint: "[supermarket] [YYYY-MM-DD]"
allowed-tools: Bash(uv run supermarket-scraper history *), Bash(uv run supermarket-scraper list)
---

# Diagnose a scrape run: $ARGUMENTS

Work from `scraper/`. Defaults: all supermarkets, today's date (Europe/Madrid).

## 1. Collect evidence (no network)

- `uv run supermarket-scraper history <code> --limit 10`: status, counters and error of recent runs.
- Read `logs/scraper-<date>.log`. Lines are text (`... | key=value`) or JSON depending on
  `SCRAPER_LOG_FORMAT`. Look for `Catalog sync failed`, `Partial scrape failure` (with `scope` and
  `reason`), `Page failed`, `Retrying request`, `Skipping delisting`, `Detail enrichment aborted`.
- If the log is missing, the scheduled task probably did not run: check Task Scheduler's
  *Last Run Result* or `systemctl status`/`journalctl -u supermarket-scraper`, and say so.

## 2. Classify

| Symptom | Likely cause | Next step |
|---|---|---|
| `SourceSchemaError ... first at <field>` | API drift | Section 3 |
| `kept returning 403`, `429` or many `Retrying request` | Blocking or rate limiting | Do not raise concurrency or evade. Report it; propose a slower schedule. |
| `failed after N attempts` / transport errors | Network or site outage | Usually transient: check the next run before changing code. |
| `returned an empty catalog` | Endpoint moved or returns nothing | Section 3 with the index endpoint |
| `Skipping delisting: too many products vanished` | Partial outage, or a real mass change | Compare `products` across runs; never lower the guard to force it. |
| `Detail enrichment aborted` | Detail endpoint changed | Section 3 with the detail endpoint |
| Unexpected exception type (not a `DomainError`) | Bug in our code | Reproduce with a test using the fixtures, then fix. |

## 3. Fix API drift

1. Ask the user before contacting the live site. With approval, fetch one sample of the failing
   endpoint with `curl -sS -A "supermarket-scraper/0.1 (+price comparison research)"` into
   `.captures/<code>/` (git-ignored), or delegate to the `supermarket-api-researcher` agent for
   larger changes. Never put personal data (the user's email, name, git identity) in the request.
2. Diff the sample against the Pydantic model in `scrapers/<code>/schemas.py` and the fixture in
   `tests/fixtures/<code>/`.
3. Update the schema, then the fixture to the new shape, then add a regression test with the new
   payload. If the change is incompatible, keep a test showing the old shape raises `SourceSchemaError`.
4. Do not make required price or id fields optional just to get past validation; a wrong price is
   worse than a failed run.

## 4. Verify and report

- Run the four checks (`ruff format --check`, `ruff check`, `mypy`, `pytest --cov --cov-fail-under=90`).
- With user approval, confirm with `uv run supermarket-scraper run <code> --dry-run --no-details`.
- Report: root cause, evidence (log lines, run ids), the fix, what was verified, and whether the
  missed day can be recovered. It cannot be backfilled: Mercadona only serves today's prices, so a
  rerun the same day is the only recovery.
