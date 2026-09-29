# 리뷰 루프 spawn 기록 hook의 모듈 실행 진입점 (python -m harness.review_loop_spawn_record)
"""모듈 실행 시 `run()`의 반환값을 종료 코드로 내보낸다."""

import sys

from harness.review_loop_spawn_record.record import run

sys.exit(run())
