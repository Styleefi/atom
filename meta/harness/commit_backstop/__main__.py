# 커밋 백스톱 hook의 모듈 실행 진입점 (python -m harness.commit_backstop)
"""`.claude/settings.json`의 PostToolUse hook command가 이 모듈을 실행한다."""

import sys

from harness.commit_backstop.backstop import run

sys.exit(run())
