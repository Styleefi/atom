# `uv run --directory meta python -m harness.rules_checker` 실행 진입점
from harness.rules_checker.check_rules import main

if __name__ == "__main__":
    raise SystemExit(main())
