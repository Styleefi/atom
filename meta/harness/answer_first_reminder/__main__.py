# answer-first 리마인더 hook의 모듈 실행 진입점 (python -m harness.answer_first_reminder)
"""`.claude/settings.json`의 UserPromptSubmit hook command가 이 모듈을 실행한다."""

import sys

from harness.answer_first_reminder.reminder import run

sys.exit(run())
