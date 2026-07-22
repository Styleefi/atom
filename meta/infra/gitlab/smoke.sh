#!/usr/bin/env bash
# 시드된 GitLab 인스턴스에서 glab 접근과 러너 파이프라인 실행을 검증하는 스모크 테스트.
set -euo pipefail

STATE_DIR="${TMPDIR:-/tmp}/atom-gitlab-infra"
# shellcheck source=/dev/null
source "$STATE_DIR/state.env"

api() {
  curl -fsS -H "PRIVATE-TOKEN: ${GITLAB_TESTENV_TOKEN}" "$@"
}

proj="$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$GITLAB_TESTENV_PROJECT")"
base="${GITLAB_TESTENV_URL}/api/v4/projects/${proj}"

# 1. glab 기본 호출 — 저장소 밖(STATE_DIR)에서 실행해 git remote 기반 호스트 추론을 차단.
#    GITLAB_HOST의 http:// 스킴은 무시되고 https로 강제되는 것을 실측(2026-07-21) —
#    격리된 GLAB_CONFIG_DIR에 api_protocol=http로 비대화식 로그인하는 것이 정도다.
#    격리는 소유자의 전역 glab 설정(~/.config/glab-cli) 오염을 막기 위한 것.
echo "[smoke] 1/2 glab basic call ..."
export GLAB_CONFIG_DIR="$STATE_DIR/glab"
export GLAB_SEND_TELEMETRY=false
glab_host="${GITLAB_TESTENV_URL#http://}"
(cd "$STATE_DIR" && \
  glab auth login --hostname "$glab_host" --token "$GITLAB_TESTENV_TOKEN" \
    --api-protocol http --git-protocol http && \
  GITLAB_HOST="$glab_host" glab issue list --repo "$GITLAB_TESTENV_PROJECT" >/dev/null)
echo "[smoke] glab OK"

# 2. 트리비얼 .gitlab-ci.yml 커밋으로 파이프라인을 트리거하고 success까지 폴링
echo "[smoke] 2/2 runner pipeline ..."
api -X POST "${base}/repository/commits" \
  -H 'Content-Type: application/json' \
  --data '{
    "branch": "main",
    "commit_message": "ci: add smoke pipeline",
    "actions": [{
      "action": "create",
      "file_path": ".gitlab-ci.yml",
      "content": "smoke:\n  image: alpine:3.22\n  script:\n    - echo smoke-ok\n"
    }]
  }' >/dev/null

deadline=$((SECONDS + 300))
while true; do
  status="$(api "${base}/pipelines?per_page=1" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["status"] if d else "none")')"
  case "$status" in
    success) break ;;
    failed|canceled|skipped)
      echo "[smoke] FAIL — pipeline status: $status; dumping job traces" >&2
      pid="$(api "${base}/pipelines?per_page=1" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"
      for jid in $(api "${base}/pipelines/${pid}/jobs" | python3 -c 'import json,sys; [print(j["id"]) for j in json.load(sys.stdin)]'); do
        echo "--- job ${jid} trace ---" >&2
        api "${base}/jobs/${jid}/trace" >&2 || true
      done
      exit 1
      ;;
  esac
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "[smoke] FAIL — pipeline not finished within 300s (last status: $status)" >&2
    exit 1
  fi
  sleep 5
done

echo "[smoke] PASS — glab call and runner pipeline both succeeded"
