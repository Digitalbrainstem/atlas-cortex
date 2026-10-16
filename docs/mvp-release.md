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
| P12 | Packaging, lifecycle, upgrade and recovery | Available software checks passed; Docker image execution pending |
| P13 | Integrated software qualification | Fixed software gates passed; full release gate blocked as detailed below |
| P14 | Truthful release handoff | Delivered; operator approval, scoped PR and release publication pending |

Repair baseline: `cd0b3dd70`, following `c48345ddc`, `8f6d0de73`,
`1520c3f84`, and `0f8c63640`. No remote push, release tag, household deployment,
or hardware sign-off is implied by these local commits.

Packaging/recovery is committed as `4c6e04590`; the final executable software
candidate and qualification harnesses are `ad7d036f3`. Subsequent handoff changes
are documentation-only. Keep these source revisions separate from the image
digest and provider/model versions that still need recording on the actual host.

## Qualification evidence

All test inputs and speaker audio were synthetic. Runs used isolated directories;
timed software checks used tmpfs to avoid confusing storage latency with asyncio
timeouts. This does **not** qualify physical-disk power loss or acoustic latency.

| Gate | Result |
|---|---|
| Final directly affected regression batch | **240 passed**, including recovery, restore, conversation, voice and core tool imports |
| A01/A02: 100 instant + 100 action requests during one blocked LLM generation | p95 **1.25 ms / 1.12 ms**, versus <=500 ms; no extra LLM calls; 100 action effects |
| A03: retained acceptance/reopen | 100 turns preserved; restore requires confirmation, not automatic replay |
| A04/A05: 10,000 durable turns | **1,048,576 bytes** incremental RSS, versus <=33,554,432; prefetch 8; second active claim rejected |
| A06/A07/A14/A16: corrections, uncertainty, backend/resource failures and recovery | Existing focused regression scenarios passed; external effects are not blindly retried |
| A08/A10: queued captures, pause/resume and reconnect | Mock transport/regression checks passed; real acoustics pending |
| A09: local cancellation/stale output | 100 cycles on one synthetic satellite agent; zero stale buffered audio; audible <=250 ms gate pending |
| A13: room/user isolation | 100 interleaved turns, two rooms/users, duplicate captures: exactly 50 timer effects per user and 50 deliveries per room |
| A15/A17/A18: policy, identity, workflows and UI | Regression checks; real Chromium reload during generation yielded one answer and receipt, with no reconnect after unmount |
| Speaker enrollment | Real Chromium upload/PCM conversion and local CPU embedding persisted and matched a synthetic profile; not recognition-accuracy qualification |
| A19: packaging/recovery | Clean tracked-source server startup/shutdown twice; WAL-safe isolated restore; pre-ledger schema upgrade/repeat/database rollback; live restore refused |
| Provider lifecycle | Real mock HTTP outage produced `/ready` 503 while `/live` stayed 200; restart restored readiness |
| Production UI | Vite build passed (Node 18.19.1) |
| A20: broad clean-source suite | **4,719 passed, 4 failed, 2 skipped, 27 deselected, 1 collection error**; intentionally not a green CI claim |
| A11/A12, Docker, Python 3.11, physical devices, real model/HA/media/NAS, operator approval | **Not qualified here** |

The full run exposed the pre-existing failures below. The final 240-test scoped
rerun additionally covers the final empty-backup rejection and lazy-import
guards. Counts from the earlier dirty-worktree suite are not directly comparable:
that workspace contained hundreds of untracked experimental tests/modules.

### Release blockers, not waived

- Clean checkout: `tests/test_coding_pipeline.py` cannot collect because tracked
  `cortex/coding/pipeline.py` imports untracked `cortex/tools/codex_pipeline.py`.
  The experimental coding subsystem is not part of household scope. Resolve its
  ownership separately; do not silently copy the untracked module into a release.
- `test_ownership_headers_present`: the unrelated `cortex/system/__init__.py`
  ownership header remains missing.
- Three avatar assertions: expressions JSON and the default/nick SVG eyebrow
  definitions. These remain visible failures, not xfails or exclusions.
- The standard non-browser CI invocation stops on that collection error. The
  broad evidence run added `--continue-on-collection-errors` to observe the rest,
  **not** to turn the run green. The legacy browser suite was not requalified;
  the focused real-browser harness passed.
- Python 3.11 and Docker are unavailable in this environment. The supported CI
  matrix still requests 3.11/3.12; only 3.12.3 was exercised locally. Docker YAML
  parsing and source/build checks are not an image-build result.
- The schema test exercises a pre-ledger database round trip, not the previous
  production binary/image. Verify the actual paired-image rollback on the host.

### Reproduce the software checks

Use a clean checkout of `ad7d036f3` or the subsequent documentation-only handoff.
Install the declared dependencies in a virtual environment and build the UI.
For timed tests on Linux, create a private tmpfs directory and point both
`TMPDIR` and `CORTEX_DATA_DIR` there. Keep a separate persistent-disk test for the
operator recovery exercise.

```bash
python3 -m pytest \
  tests/test_mvp_packaging.py tests/test_recovery.py tests/test_state_snapshot.py \
  tests/test_offsite_backup.py tests/test_voice_backup.py \
  tests/test_conversation.py tests/test_voice_turns.py \
  tests/test_function_calling.py tests/test_code_sandbox.py -q
python3 -m scripts.qualify_mvp --output /absolute/private/resource-result.json
cd admin && npm ci && npm run build && cd ..
python3 -m playwright install chromium
python3 -m scripts.mvp_smoke --browser

# Standard CI-equivalent non-browser invocation (currently blocked):
python3 -m pytest tests/ -q --tb=short -m "not browser" \
  --ignore=tests/test_ui_browser.py
```

The resource script fixes its corpus, 10,000-turn count, one-active/eight-prefetch
profile and 32 MiB incremental RSS threshold. The saturated fast-path test fixes
100 requests per route and <=500 ms p95. Do not reinterpret filler latency as
answer latency. The smoke harness starts temporary local mock services, creates
a temporary household DB, and never provisions physical satellites. The resource
and browser/lifecycle commands are now part of the existing CI workflow; this
local work did not trigger or claim a successful remote workflow.

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

Replace placeholders, protect the file with mode 0600, change the initial admin
password, and record the actual model IDs/digests and provider versions. Do not
expose the service publicly. Native startup does not automatically source an env
file: load your own trusted file into the environment before launching.

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

## Scoped PR handoff

Suggested title: **Household MVP durable turns, offline recovery and software
qualification**. Include the prerequisite P00-P11 and repair commits already on
the branch, plus `4c6e04590` and `ad7d036f3`; check the actual diff against the
chosen base before creating the PR. This is not authorization to merge the entire
experimental branch.

The PR description should include the evidence table, the non-green CI blockers,
the deferred physical/platform checklist and the recovery procedure. No push,
PR creation, tag or deployment was performed for this handoff. A household MVP
release requires those blockers to be resolved and operator gates to be signed
off, not simply a change of the phase labels to "complete".
