# Household MVP candidate: phase status and operator handoff

This is the authoritative execution status for the household MVP, superseding
historical "complete" labels in design documents. It is **not a production
release or a claim of physical household qualification**. Nexus Academy and the
untracked experimental agent/model work are outside this release.

## Phase status

| Phase | Scope | Status |
|---|---|---|
| P00 | Baseline, ownership and acceptance contract | Implemented; baseline regressions remain recorded |
| P01 | Stage-aware dispatch contracts | Implemented and regression-tested |
| P02 | Fast paths before scarce LLM work | Implemented; integrated measurements below |
| P03 | Durable SQLite turn admission and bounded pending work | Implemented and persistence-tested |
| P04 | Bounded inference and explicit overload | Implemented and regression-tested |
| P05 | Durable LLM conversations, corrections and isolation | Implemented; review defects repaired |
| P06 | Independent finalized voice admission | Implemented; slow-plugin blocking repaired |
| P07 | Interruption, sentence resume and stale-output suppression | Implemented in software; audible-stop gate pending |
| P08 | Two-satellite path and capture contract | Software checks implemented; two-Pi/ReSpeaker gate pending |
| P09 | Household workflows, notifications and memory | Implemented; dispatch and memory integration repaired |
| P10 | Admin/public chat, durable result recovery and receipts | Implemented; production browser checks passed in repair batch |
| P11 | Policy, identity, privacy and deletion | Implemented; persisted auth, child tier, speech and deletion repaired |
| P12 | Packaging, lifecycle, upgrade and recovery | 78 targeted checks passed; Docker image execution pending |
| P13 | Integrated software qualification | In progress; physical and external-service qualification pending |
| P14 | Truthful release handoff | This guide is being finalized; operator sign-off remains pending |

Repair baseline: `cd0b3dd70`, following `c48345ddc`, `8f6d0de73`,
`1520c3f84`, and `0f8c63640`. No remote push, release tag, household deployment,
or hardware sign-off is implied by these local commits.

## Supported candidate configuration

The locally exercised platform is Linux x86-64, Python 3.12, SQLite WAL/FULL
synchronous, Node 18.19.1 for Vite, and Chromium. The Dockerfile targets
Python 3.12 and Node 20. Other Python versions, operating systems, Docker
execution and real model/provider versions require their own qualification.

| Component | Candidate setting | Evidence boundary |
|---|---|---|
| LLM | `openai_compatible`, explicit HTTP endpoint/models | Real HTTP transport to local mocks; not model-quality/GPU certification |
| Voice output | Kokoro HTTP, `af_bella` | Mock transport and browser playback; real voice/acoustic quality pending |
| Voice input | `whisper_cpp`, explicit host/port | Mock HTTP contract; real recognition accuracy pending |
| Speaker identity | Local CPU Resemblyzer, persisted enrollment | CPU extraction/enrollment exercised; household accuracy pending |
| Memory | SQLite FTS5; optional Chroma | User-scoped recall/write/deletion tested; Chroma consistency requires stopped writers |
| Other LLM/TTS providers, HA/media/NAS | Optional integrations | Not blanket-qualified by the mock tests |

Low-resource policy is fixed at one active inference and prefetch eight; pending
turns are durable records, not unlimited resident tasks. Defaults are 64 MiB
ledger payload budget, 16 MiB minimum free disk, 65,536 input bytes and seven-day
retention for eligible terminal records. Inspect the remaining settings in
`TurnStorePolicy`; do not silently loosen limits to pass a test.

## Minimal install

Use a clean checkout of the candidate revision, not an archive of a dirty
workspace. Core memory imports no longer require the untracked sessions and
compaction extensions.

For a native installation, use a virtual environment, install
`requirements.txt`, build the UI with `cd admin && npm ci && npm run build`,
then run `python3 -m cortex.server` from the repository root with the environment
below loaded. Resemblyzer's `webrtcvad` dependency needs a C compiler when no
wheel is available. The Dockerfile provides and removes that build dependency.

The standalone [minimal Compose file](../docker/docker-compose.mvp.yml) starts
only Cortex. Supply external LLM/STT/TTS endpoints; this deliberately does not
launch every GPU TTS service. Create a private absolute-path `cortex.env`:

```dotenv
CORTEX_PORT=5100
CORTEX_JWT_SECRET=REPLACE_WITH_A_UNIQUE_RANDOM_SECRET
LLM_PROVIDER=openai_compatible
LLM_URL=http://127.0.0.1:8080
MODEL_FAST=REPLACE_WITH_SERVED_MODEL_ID
MODEL_THINKING=REPLACE_WITH_SERVED_MODEL_ID
TTS_PROVIDER=kokoro
KOKORO_HOST=127.0.0.1
KOKORO_PORT=8880
KOKORO_VOICE=af_bella
STT_BACKEND=whisper_cpp
STT_HOST=127.0.0.1
STT_PORT=10300
CORTEX_TURN_MAX_ACTIVE=1
CORTEX_TURN_PREFETCH=8
```

Replace placeholders, protect the file with mode 0600, and record the actual
model IDs/digests and provider versions. Do not expose the service publicly.

```bash
export ATLAS_ENV_FILE=/absolute/private/cortex.env
export ATLAS_DATA_DIR_HOST=/absolute/persistent/atlas-data
docker compose -f docker/docker-compose.mvp.yml config
docker compose -f docker/docker-compose.mvp.yml build \
  --build-arg CORTEX_BUILD_REVISION="$(git rev-parse HEAD)"
# Run only after operator approval and successful image qualification:
docker compose -f docker/docker-compose.mvp.yml up -d
```

`/live` is process liveness and never contacts a provider. `/ready` requires
completed startup, readable durable storage and a reachable LLM, returning 503
when degraded. STT/TTS/satellites are explicitly `not_checked` there: run the
voice checks before calling the household voice system ready. Legacy `/health`
retains its existing provider-status payload; it is not the Docker liveness gate.

Back up before upgrading. See [MVP recovery](mvp-recovery.md) for isolated
restore, stopped-service ownership, configuration capture and compatible rollback.

## Operator gates still required

- Build/run the Docker candidate on the intended host; pin and record image and
  model digests. Check fresh install, restart, degraded providers and recovery.
- Enroll consenting household speakers; verify unknown/ambiguous speakers remain
  anonymous and child-safe. Recognition is not an authentication substitute.
- Connect the two intended Pi/ReSpeaker satellites. Record firmware, room,
  microphone distance, noise, gain, playback volume and network conditions.
- Per setup, run at least 100 positive wakes (20 cold) with >=95% success, and
  10,000 background windows with <=0.1% false activation.
- Measure audible interruption over 100 cycles: p95 <=250 ms and no stale audio.
  Software task cancellation or sending a stop frame is not that measurement.
- Run 100 interleaved two-room turns and actual household workflows; verify no
  cross-room/user output or duplicate external effects.
- Test real HA/media integrations, provider outage/reconnect, persistent-disk
  crash recovery, NAS backup/restore and explicit recovery confirmation.
- Review baseline failures and limitations, then obtain operator sign-off before
  a household MVP tag/deployment. Prepare a scoped PR; branch protection remains
  in force. Do not mix Nexus Academy or unrelated workspace experiments into it.
