# 리뷰 루프 트레일러 검사 hook의 모듈 실행 진입점 (python -m harness.review_loop_trailer_check)
"""모듈 실행 시 `run()`의 반환값을 종료 코드로 내보낸다."""

import sys

from harness.review_loop_trailer_check.check import run

sys.exit(run())
