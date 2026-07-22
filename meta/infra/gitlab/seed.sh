#!/usr/bin/env bash
# GitLab CE 테스트 인스턴스를 백지 상태에서 결정적으로 프로비저닝하는 시드 스크립트.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${TMPDIR:-/tmp}/atom-gitlab-infra"
STATE_FILE="$STATE_DIR/state.env"

GITLAB_URL="http://localhost:8929"
# 러너·job 컨테이너가 compose 네트워크 안에서 GitLab에 접근할 때 쓰는 주소
INTERNAL_URL="http://gitlab:8929"
NETWORK="atom-gitlab-infra_default"
# 테스트 전용 고정 토큰 — 인스턴스가 루프백 전용 + 일회성이라 유출 표면이 없다
PAT="atom-testenv-pat-00000000000000000000"
PROJECT="scratch"

cd "$SCRIPT_DIR"

# 1. gitlab 컨테이너 health 대기.
#    /-/readiness HTTP 폴링은 모니터링 화이트리스트(컨테이너 내부 127.0.0.0/8)에
#    막히므로 이미지 내장 HEALTHCHECK 상태를 docker inspect로 읽는다.
echo "[seed] waiting for gitlab to become healthy (first boot: 4-6 min) ..."
deadline=$((SECONDS + 600))
while true; do
  cid="$(docker compose ps -q gitlab)"
  status="$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo missing)"
  [ "$status" = "healthy" ] && break
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "[seed] ERROR: gitlab not healthy within 600s (last status: $status)" >&2
    exit 1
  fi
  sleep 10
done
echo "[seed] gitlab healthy"

# 1b. 호스트→컨테이너 HTTP 경로 재확인 — 컨테이너 내부 health가 통과해도 첫 부팅
#     직후 reconfigure 중에는 호스트 경유 연결이 잠시 reset될 수 있다(실측, 2026-07-21).
echo "[seed] waiting for http endpoint from host ..."
deadline=$((SECONDS + 120))
until curl -fsS -o /dev/null "${GITLAB_URL}/users/sign_in" 2>/dev/null; do
  if [ "$SECONDS" -ge "$deadline" ]; then
    echo "[seed] ERROR: gitlab http endpoint not reachable from host within 120s" >&2
    exit 1
  fi
  sleep 5
done
echo "[seed] http endpoint reachable"

# 2. root PAT 생성 — rails runner 1회 호출로 UI 없이 결정적으로 만든다
echo "[seed] creating root personal access token ..."
docker compose exec -T gitlab gitlab-rails runner "
  user = User.find_by_username('root')
  token = user.personal_access_tokens.create!(scopes: ['api'], name: 'atom-seed', expires_at: 30.days.from_now)
  token.set_token('${PAT}')
  token.save!
"

# 3. 스크래치 프로젝트 생성
echo "[seed] creating scratch project ..."
curl -fsS --retry 5 --retry-delay 3 --retry-all-errors -X POST -H "PRIVATE-TOKEN: ${PAT}" \
  "${GITLAB_URL}/api/v4/projects" \
  --data "name=${PROJECT}&initialize_with_readme=true" >/dev/null

# 4. 인스턴스 러너 생성 + 등록.
#    run_untagged=true 필수 — 기본값 false면 태그 없는 스모크 job이 영원히 pending.
#    ⚠️ 보안 불변식(compose.yaml 상단 경고 참조): --docker-privileged를 추가하거나
#    job 컨테이너에 docker.sock을 volumes로 전달하는 변경 금지. job은 반드시
#    비특권 형제 컨테이너로 격리된 채 실행되어야 한다.
echo "[seed] creating and registering runner ..."
runner_token="$(curl -fsS --retry 5 --retry-delay 3 --retry-all-errors -X POST -H "PRIVATE-TOKEN: ${PAT}" \
  "${GITLAB_URL}/api/v4/user/runners" \
  --data "runner_type=instance_type&run_untagged=true&description=atom-smoke" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
docker compose exec -T runner gitlab-runner register --non-interactive \
  --url "${INTERNAL_URL}" \
  --token "${runner_token}" \
  --executor docker \
  --docker-image alpine:3.22 \
  --docker-network-mode "${NETWORK}" \
  --clone-url "${INTERNAL_URL}"

# 5. 상태 파일 기록 — 저장소 밖 /tmp 경로라 커밋이 구조적으로 불가능
umask 077
mkdir -p "$STATE_DIR"
cat > "$STATE_FILE" <<EOF
GITLAB_TESTENV_URL=${GITLAB_URL}
GITLAB_TESTENV_TOKEN=${PAT}
GITLAB_TESTENV_PROJECT=root/${PROJECT}
EOF
chmod 600 "$STATE_FILE"
echo "[seed] done — state written to $STATE_FILE"
