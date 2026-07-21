# GitLab test environment

**On-demand test infrastructure — never runs persistently.**

A disposable GitLab CE + gitlab-runner stack for verifying atom's forge-coupled artifacts against a real GitLab instance: the glab adapter of issue-duplicate-guard (#9) and `.gitlab-ci.yml` execution (#10). Mocked unit tests cannot detect real-world drift (glab CLI output changes, GitLab server upgrades), and the adapters fail open — this environment makes that verification reproducible on demand.

## ⚠️ Security model — READ THIS BEFORE REUSING OR MODIFYING (humans and agents alike)

The `runner` service mounts `/var/run/docker.sock`, which is **equivalent to root on the host docker daemon**. This is safe here ONLY because all four conditions below hold simultaneously. If your change breaks ANY of them, stop and redesign — do not proceed.

1. **Loopback-only**: GitLab binds to `127.0.0.1:8929`; nothing outside this machine can reach it.
2. **Ephemeral**: `run.sh` tears everything down (`down -v`) after every run; the stack never runs persistently.
3. **Self-authored jobs only**: every CI job executed here is written locally by this repository; there are no external users.
4. **Unprivileged jobs**: job containers run as non-privileged siblings and never receive the docker socket (see `seed.sh` registration).

**Forbidden changes** (each one breaks a condition above):

- Do NOT copy this stack or runner registration as a persistent, shared, or production runner setup.
- Do NOT remove the `127.0.0.1` prefix from the port binding.
- Do NOT add `--docker-privileged` to the runner registration or mount the docker socket into job containers.
- Do NOT add a `restart:` policy or otherwise keep the stack running between test sessions.

Alternatives were reviewed and rejected on 2026-07-21 (see issue #17): dind requires `privileged` (worse), rootless docker is impractical under Docker Desktop, socket proxies cannot filter the one endpoint the executor needs, and an in-container shell executor would ignore `image:` and defeat the fidelity this environment exists for.

## Usage

```bash
# full lifecycle in one call: preclean → up → seed → smoke → down
./run.sh

# run an arbitrary payload while the environment is up (teardown guaranteed):
./run.sh uv run --directory meta pytest -m gitlab   # e.g. #9 integration tests
```

Manual steps for debugging:

```bash
docker compose up -d
./seed.sh          # health wait → root PAT → scratch project → runner registration
./smoke.sh         # glab call + trivial pipeline on the runner
docker compose down -v   # -v is mandatory: the image declares VOLUMEs, so plain
                         # `down` leaks multi-GB volumes on every single run
```

## What seed provides

- Root PAT (scope `api`) and a scratch project `root/scratch`.
- An instance runner (docker executor, `run_untagged=true`) wired to the compose network.
- A state file at `${TMPDIR:-/tmp}/atom-gitlab-infra/state.env` (`GITLAB_TESTENV_URL` / `GITLAB_TESTENV_TOKEN` / `GITLAB_TESTENV_PROJECT`) for payloads to source. It lives outside the repository so it can never be committed; `run.sh` deletes it on teardown.

## Notes

- **Credentials are fixed and test-only.** They expose nothing because of conditions 1–2 above; they would be a real leak in any persistent or shared deployment.
- GitLab CE is pinned to `19.2.0-ce.0` (latest stable at creation). TODO(#17): align the pin with the production CE version once confirmed. glab CLI is deliberately installed unpinned so upstream CLI drift surfaces here first.
- Run glab against this instance from outside the atom repository (the state dir works) — inside the repo, glab may infer a host from the GitHub remotes.
- If a run crashes *mid-job*, runner-spawned job containers (not compose-managed) can linger; check `docker ps -a`. The preclean in `run.sh` resets the compose stack itself.
- First boot pulls ~2.7 GB of images and takes 4–6 minutes; subsequent runs boot faster.
