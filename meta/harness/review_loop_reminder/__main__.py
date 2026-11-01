# review-loop 리마인더 hook의 모듈 실행 진입점 (python -m harness.review_loop_reminder)
"""`.claude/settings.json`의 PostToolUse(Bash) hook command가 이 모듈을 실행한다."""

import sys

from harness.review_loop_reminder.reminder import run

sys.exit(run())
