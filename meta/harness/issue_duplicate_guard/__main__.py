# 이슈 중복 방지 hook의 모듈 실행 진입점 (python -m harness.issue_duplicate_guard)
"""`.claude/settings.json`의 PreToolUse hook command가 이 모듈을 실행한다."""

import sys

from harness.issue_duplicate_guard.guard import run

sys.exit(run())
