# Change workflow

Applies to every task in this repository. CLAUDE.md lists the commands; this file says when to use them.

## Before editing

- Read the module you are changing and its tests. Read `scraper/README.md` before any change that
  crosses layers, touches the schema or adds a supermarket.
- For a bug, first write a failing test that reproduces it, then fix it. Every fix lands with that test.
- Keep diffs focused. Do not refactor, rename or reformat unrelated code in the same change.

## Never do without being asked

- Run `supermarket-scraper run` without `--dry-run`, or anything else that calls a real supermarket.
- Touch `scraper/data/`, `scraper/.env` or `scraper/uv.lock` by hand.
- Weaken a quality gate to make it pass: no new Ruff ignores, no `# noqa` or `# type: ignore`
  without a named code and a reason in the commit message, no lowering `--cov-fail-under`,
  no skipping or deleting failing tests.
- Delete products, categories or price history, in code or in a database.

## Keep docs in sync in the same change

| You changed | Also update |
|---|---|
| A setting in `config.py` or a chain's `*Settings` | `.env.example`, README "Configuration" table |
| A CLI command, flag or exit code | README "CLI", CLAUDE.md "Commands" |
| The database schema | README "Data model", and the migration note in `persistence.md` |
| A layer boundary, port or extension point | README "Architecture", CLAUDE.md "Architecture" |
| A daily runner or deploy file | README "Daily execution" |

## Finishing

Run, from `scraper/`: `ruff format --check .`, `ruff check .`, `mypy`, `pytest --cov --cov-fail-under=90`.
In the summary, state what was verified and what was not. Tests use recorded fixtures, so say
explicitly when behaviour against the live API is unverified.
