#!/usr/bin/env bash
# 저장소 루트의 .gitlab-ci.yml을 시드된 일회용 GitLab 인스턴스에서 실검증하는 페이로드.
# 실행: cd meta/infra/gitlab && ./run.sh ./verify_ci.sh
# 검증 항목: (1) CI lint 통과 (2) 기본 브랜치 파이프라인 success
#            (3) 일반 브랜치 푸시 파이프라인 억제 (4) MR 파이프라인 success
set -euo pipefail

STATE_DIR="${TMPDIR:-/tmp}/atom-gitlab-infra"
# shellcheck source=/dev/null
source "$STATE_DIR/state.env"

# 대상 검증 가드: 아래에서 main 보호 해제와 force-push라는 파괴적 작업을 수행하므로,
# 대상이 일회용 스택의 고정 루프백 주소가 아니면 즉시 중단한다.
if [ "${GITLAB_TESTENV_URL}" != "http://localhost:8929" ]; then
  echo "[verify-ci] ABORT — target is not the disposable stack: ${GITLAB_TESTENV_URL}" >&2
  exit 1
fi

api() {
  curl -fsS -H "PRIVATE-TOKEN: ${GITLAB_TESTENV_TOKEN}" "$@"
}

proj="$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$GITLAB_TESTENV_PROJECT")"
base="${GITLAB_TESTENV_URL}/api/v4/projects/${proj}"

repo_root="$(git rev-parse --show-toplevel)"
ci_file="${repo_root}/.gitlab-ci.yml"
[ -f "$ci_file" ] || { echo "[verify-ci] FAIL — ${ci_file} not found" >&2; exit 1; }

# push는 HEAD 커밋만 반영하므로, 미커밋 변경은 검증 대상에서 빠진다는 사실을 알린다.
if [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
  echo "[verify-ci] WARN — working tree dirty; only the HEAD commit gets pushed/verified" >&2
fi

# 실패한 파이프라인의 잡 트레이스를 stderr로 덤프한다(smoke.sh 패턴).
dump_traces() {
  local pid="$1"
  for jid in $(api "${base}/pipelines/${pid}/jobs" | python3 -c 'import json,sys; [print(j["id"]) for j in json.load(sys.stdin)]'); do
    echo "--- job ${jid} trace ---" >&2
    api "${base}/jobs/${jid}/trace" >&2 || true
  done
}

# 파이프라인 목록 엔드포인트를 폴링해 최신 1건이 success가 될 때까지 기다린다.
# 인자: 목록 URL, 시한(초), 라벨. 실패/시한 초과 시 트레이스 덤프 후 종료한다.
poll_success() {
  local url="$1" timeout="$2" label="$3" deadline status pid
  deadline=$((SECONDS + timeout))
  while true; do
    status="$(api "$url" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["status"] if d else "none")')"
    case "$status" in
      success)
        pid="$(api "$url" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"
        echo "[verify-ci] ${label} OK — pipeline ${pid} success"
        return 0
        ;;
      failed|canceled|skipped)
        echo "[verify-ci] FAIL — ${label} pipeline status: ${status}; dumping job traces" >&2
        pid="$(api "$url" | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"
        dump_traces "$pid"
        exit 1
        ;;
    esac
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "[verify-ci] FAIL — ${label} pipeline not finished within ${timeout}s (last status: ${status})" >&2
      exit 1
    fi
    sleep 5
  done
}

# 1. CI lint — 문법·스키마를 파이프라인 실행 전에 확인한다.
echo "[verify-ci] 1/4 ci lint ..."
lint_payload="$(python3 -c 'import json,sys; print(json.dumps({"content": open(sys.argv[1]).read()}))' "$ci_file")"
lint_ok="$(api -X POST "${base}/ci/lint" -H 'Content-Type: application/json' --data "$lint_payload" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("true" if d.get("valid") else json.dumps(d.get("errors")))')"
[ "$lint_ok" = "true" ] || { echo "[verify-ci] FAIL — lint errors: ${lint_ok}" >&2; exit 1; }
echo "[verify-ci] lint OK"

# 2. 기본 브랜치 경로 — HEAD를 scratch의 main으로 밀어 넣고 파이프라인 success를 확인한다.
#    seed된 main은 무관한 README 이력 + 기본 보호 상태이므로 보호를 풀고 force-push한다.
#    (보호 해제는 set -e 아래에서 404(비보호)만 허용 — 그 외 오류는 실패로 처리)
echo "[verify-ci] 2/4 default-branch pipeline ..."
code="$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE \
  -H "PRIVATE-TOKEN: ${GITLAB_TESTENV_TOKEN}" "${base}/protected_branches/main")"
case "$code" in
  204|404) ;;
  *) echo "[verify-ci] FAIL — unprotect main returned HTTP ${code}" >&2; exit 1 ;;
esac
# 토큰이 argv에 노출되는 것은 루프백 전용·매 실행 파기되는 PAT라 수용(smoke.sh의 curl 선례와 동일).
# push URL은 로그로 출력하지 않는다.
push_url="http://root:${GITLAB_TESTENV_TOKEN}@${GITLAB_TESTENV_URL#http://}/${GITLAB_TESTENV_PROJECT}.git"
git -C "$repo_root" push --force --quiet "$push_url" HEAD:main
poll_success "${base}/pipelines?ref=main&per_page=1" 600 "default-branch"

# 3. 브랜치 파이프라인 억제 — MR 없는 일반 브랜치 푸시에는 파이프라인이 생기지 않아야 한다.
#    파이프라인 생성은 비동기라 부정 단언은 원리상 거짓 통과 가능성이 남는다(짧은 구간 반복 확인으로 완화).
echo "[verify-ci] 3/4 branch-pipeline suppression ..."
api -X POST "${base}/repository/commits" -H 'Content-Type: application/json' --data '{
  "branch": "ci-verify",
  "start_branch": "main",
  "commit_message": "chore: trivial commit for suppression check",
  "actions": [{"action": "create", "file_path": "ci-verify-marker.txt", "content": "marker\n"}]
}' >/dev/null
for _ in 1 2 3 4 5 6; do
  sleep 5
  count="$(api "${base}/pipelines?ref=ci-verify" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
  if [ "$count" != "0" ]; then
    echo "[verify-ci] FAIL — branch push created ${count} pipeline(s); suppression broken" >&2
    exit 1
  fi
done
echo "[verify-ci] suppression OK — no pipeline for plain branch push (30s window)"

# 4. MR 경로 — ci-verify → main MR을 만들고 merge_request_event 파이프라인 success를 확인한다.
#    (MR 전용 엔드포인트로 조회해 다른 ref 파이프라인과의 혼동을 차단)
echo "[verify-ci] 4/4 merge-request pipeline ..."
mr_iid="$(api -X POST "${base}/merge_requests" -H 'Content-Type: application/json' --data '{
  "source_branch": "ci-verify",
  "target_branch": "main",
  "title": "verify: gitlab-ci merge_request_event pipeline"
}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["iid"])')"
poll_success "${base}/merge_requests/${mr_iid}/pipelines" 600 "merge-request"

echo "[verify-ci] PASS — lint, default-branch pipeline, branch suppression, MR pipeline all verified"
