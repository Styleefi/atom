# `uv run --directory meta python -m harness.branch_protection` 실행 진입점
from harness.branch_protection.protection import main

if __name__ == "__main__":
    raise SystemExit(main())
