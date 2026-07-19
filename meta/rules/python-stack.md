---
id: python-stack
tier: convention
enforce: claude-md
deployed-to: CLAUDE.md
---

# Python stack (meta layer only)

Scope: Python code under `meta/` (harnesses etc.) in atom and its child projects. Child projects' **product** code is NOT governed by this rule — each project declares its own stack in its CLAUDE.md "Toolchain & conventions" section.

- Python >= 3.12 (pinned via `requires-python` in `meta/pyproject.toml`)
- Environment: **uv**, with `meta/` as a self-contained uv project (own `pyproject.toml`, committed `uv.lock`, own `.venv`). The meta environment is fully isolated from the product environment, so meta and product dependency versions (e.g. pytest) can never conflict.
- Run: `uv run --directory meta pytest` / `uv run --directory meta python -m harness.rules_checker`
- Tests: pytest; every harness ships its own `tests/` package (with `__init__.py` to avoid cross-harness module-name collisions)
- Docstrings: Google style, written in Korean (code identifiers stay English)
- Dependencies: stdlib first, keep minimal; floors at the current major (e.g. `pytest>=9.0`)
