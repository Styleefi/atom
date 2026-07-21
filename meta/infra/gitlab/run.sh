#!/usr/bin/env bash
# GitLab 테스트 환경의 전체 수명 주기(프리클린→up→seed→페이로드→down)를 한 번에 실행하는 래퍼.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CALLER_DIR="$(pwd)"
STATE_DIR="${TMPDIR:-/tmp}/atom-gitlab-infra"

cleanup() {
  cd "$SCRIPT_DIR"
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$STATE_DIR"
  echo "[run] teardown complete (containers, volumes, state file removed)"
}
# 페이로드 실패를 포함한 모든 종료 경로에서 down -v를 보장한다 (중복 down은 멱등)
trap cleanup EXIT

cd "$SCRIPT_DIR"

# 프리클린 — 재부팅·강제 종료가 남긴 이전 실행 잔여물을 제거하고 항상 백지에서 시작
docker compose down -v --remove-orphans

# 포트 선점 검사 — 프리클린 후에도 8929가 살아 있으면 외부 프로세스 소유
if (exec 3<>/dev/tcp/127.0.0.1/8929) 2>/dev/null; then
  echo "[run] ERROR: port 8929 is already in use by another process — aborting" >&2
  exit 1
fi

docker compose up -d
./seed.sh

# 페이로드 — 기본은 smoke, 인자가 있으면 호출자의 원래 cwd에서 해당 명령 실행
if [ "$#" -eq 0 ]; then
  ./smoke.sh
else
  (cd "$CALLER_DIR" && "$@")
fi
echo "[run] payload finished — tearing down"
