# 커밋 규율 hook의 모듈 실행 진입점 (python -m harness.commit_guard)
"""`.claude/settings.json`의 PreToolUse hook command가 이 모듈을 실행한다."""

import sys

from harness.commit_guard.guard import run

sys.exit(run())
