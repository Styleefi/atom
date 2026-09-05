# `uv run --directory meta python -m harness.commit_publication` 실행 진입점
from harness.commit_publication.check import main

if __name__ == "__main__":
    raise SystemExit(main())
