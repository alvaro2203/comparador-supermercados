---
paths:
  - "scraper/pyproject.toml"
  - "scraper/uv.lock"
  - ".pre-commit-config.yaml"
  - ".github/workflows/ci.yml"
---

# Dependencies and tooling

## Dependencies

- Manage them with uv only: `uv add <pkg>`, `uv add --group dev <pkg>`, `uv remove <pkg>`.
  Never edit `uv.lock` by hand, and never use `pip`.
- Every runtime dependency has a lower and an upper bound (`"httpx>=0.27,<1.0"`).
- Database drivers are optional extras (`postgres`, `mysql`), never core dependencies.
- Keep platform markers where they exist (`tzdata` is required on Windows).
- Prefer the standard library. A new dependency needs a reason a reviewer would accept, and must ship
  type hints or stubs: mypy runs with `disallow_any_unimported`.
- After changing dependencies, confirm `uv lock --check` passes and the four CI checks still pass.

## Tool configuration

- Do not relax `[tool.ruff]`, `[tool.mypy]`, `[tool.pytest.ini_options]` or coverage settings to
  get a change through. Tightening them is welcome.
- Do not add global Ruff ignores. Test-only exemptions belong in `per-file-ignores` for `tests/**`.
- The CI workflow, pre-commit hooks and CLAUDE.md "Commands" must run the same checks in the same
  order. Change all three together.
- CI runs on Linux and Windows; anything you add must work on both.
- Both files live at the repository root, which has no `pyproject.toml`. CI steps run with
  `working-directory: scraper`; pre-commit hooks use `uv run --project scraper` (Ruff, which
  receives root-relative file names) or `uv run --directory scraper` (mypy, whose config paths are
  relative to `scraper/`). Keep that split when adding hooks.
