---
name: verify-jumperfree
description: >-
  Verify JumperFree's primary PC surface — the stdlib-only Python CLI
  (`python3 -m jumperfree` / `jumperfree`) for routing, switch plans, and
  Clip LED host guide. Use when proving a change still works end-to-end on
  the machine, before merging route/plan/host work, or when CI smoke alone
  is not enough. Does not cover on-device firmware or real UART hardware.
---

# Verify JumperFree

Agent-facing playbook. Read cold: launch an isolated CLI workspace, doctor it, drive one mapped feature the way a user would type commands, keep evidence, then clean scratch state.

Repo root for this skill: three parents up from this file (`.cursor/skills/verify-jumperfree` → repo). Helpers resolve it automatically.

Primary surface: **CLI / library** (no web UI). Secondary paths that exist but are out of scope for this skill until they ship on PC end-to-end: firmware under future `fw/`, real serial ports (optional `pyserial`).

## Launch

There is no long-lived server. Launch means: create an isolated state directory, confirm the package imports, and record run metadata under the evidence tree.

```bash
cd /path/to/JumperFree
./.cursor/skills/verify-jumperfree/helpers/launch.sh
```

Ready when stdout contains `LAUNCH_OK` and the printed `STATE_ROOT` has a `.ready` file. Export the three `JF_VERIFY_*` lines the script prints so later helpers reuse the same run.

Optional overrides:

- `JF_VERIFY_PYTHON` — interpreter (default `python3`, must be ≥ 3.11)
- `JF_VERIFY_RUN_ID` — fixed run id (default timestamp-pid)
- `JF_VERIFY_STATE_ROOT` — scratch dir (default `/tmp/jumperfree-verify-$RUN_ID`)
- `JF_VERIFY_EVIDENCE_DIR` — proof dir (default `<skill>/evidence/$RUN_ID`)

Teardown of scratch state is `helpers/cleanup.sh` (see Cleanup). Do not leave `JF_VERIFY_STATE_ROOT` pointing at the repo's `out/` directory.

## Doctor

Read-only. Run whenever anything looks off, and always before Drive.

```bash
export JF_VERIFY_RUN_ID=...   # from launch, if you already launched
./.cursor/skills/verify-jumperfree/helpers/doctor.sh
```

Requires: Python ≥ 3.11, `import jumperfree` from the repo (via `PYTHONPATH` set by helpers), `python3 -m jumperfree --help` listing `route` and `host`. If a launch already created `STATE_ROOT`, doctor also checks `.ready` and writability.

Fail phrases start with `DOCTOR_FAIL`. Success ends with `DOCTOR_OK doctor_complete`.

## Drive

Prefer helpers for mapped features; otherwise type the literal CLI from the feature file under `features/`. Always pass `--state` (and plan `--new`/`--old`) under `$JF_VERIFY_STATE_ROOT` so runs do not clobber `out/`.

Harness: shell + `python3 -m jumperfree` (helpers wrap this as `jf`). Stable handles are subcommand names and stdout markers:

- Route success lines: `ROUTE ...` plus a `ROUTE_JSON {...}` line
- Plan success: `PLAN OK ...` plus `PLAN_JSON {...}` (may print the unverified-board banner first)
- Host success: `HOST ... OK` plus `HOST_JSON {...}`

Example — drive the mapped stage-1 route feature (used for the initial proof):

```bash
# after launch.sh, with JF_VERIFY_* exported
./.cursor/skills/verify-jumperfree/helpers/drive-stage1-route.sh
```

Other features: follow `features/*.md` recipes with the same env. Host recipes must use `--mock` on this skill's PC path; `--port` needs hardware and optional `pyserial` and is not proven here.

Never drive against a state directory you did not create with `launch.sh` for this run.

## Evidence

Proof root (survives cleanup):

```text
.cursor/skills/verify-jumperfree/evidence/<run_id>/
```

Standards:

- Exercise the real CLI path (`python3 -m jumperfree ...`), not internal Python setters.
- Capture the command transcript (action) and a second read (`query` / `list` / saved JSON) showing resulting state.
- Side effects: state JSON under `$JF_VERIFY_STATE_ROOT`, copied into the feature's evidence folder when the drive finishes.
- Host `--mock` is the production boundary for "no board attached"; do not treat a mock session as proof of UART or firmware.
- Record feature id and entry point in each feature's `PROOF.txt`.

Unit-test / coverage suite (`python3 -m unittest discover -s tests -t .` and `python3 scripts/coverage_report.py`) is complementary CI evidence, not a substitute for driving a mapped user feature.

## Cleanup

```bash
./.cursor/skills/verify-jumperfree/helpers/cleanup.sh
```

Removes only `/tmp/jumperfree-verify-*` scratch trees created for this run. Refuses any other `STATE_ROOT`. **Does not delete** `evidence/<run_id>/`.

After failed Drive attempts, run cleanup too so scratch dirs do not accumulate; keep evidence from failed runs for diagnosis or delete those evidence folders manually if desired.

## Helpers

All under `helpers/`, executable:

| Script | Role |
| --- | --- |
| `common.sh` | Sourced by others; sets `REPO_ROOT`, `STATE_ROOT`, `EVIDENCE_DIR`, `jf` |
| `launch.sh` | Create scratch + evidence dirs; write launch meta |
| `doctor.sh` | Read-only readiness |
| `drive-stage1-route.sh` | Drive feature `stage1-route` end-to-end |
| `cleanup.sh` | Remove scratch state only |

Invocation pattern:

```bash
cd /path/to/JumperFree
./.cursor/skills/verify-jumperfree/helpers/launch.sh
# export the JF_VERIFY_* lines it prints
./.cursor/skills/verify-jumperfree/helpers/doctor.sh
./.cursor/skills/verify-jumperfree/helpers/drive-stage1-route.sh
./.cursor/skills/verify-jumperfree/helpers/cleanup.sh
ls .cursor/skills/verify-jumperfree/evidence/$JF_VERIFY_RUN_ID/stage1-route/PROOF.txt
```

## Feature map

Index: [`features/README.md`](features/README.md). Seeded features:

1. `stage1-route` — full-connect net init/create/query/list/delete
2. `v01-xbar-route` — V0.1 crossbar Steiner create/query
3. `switch-plan` — `route plan` break-before-make compile
4. `host-led-mock` — Clip LED set/clear/status/guide with `--mock`
5. `xbar-rearrange` — bounded rearrange on create (`--rearrange`)

## Gaps (honest)

- Firmware C core / on-target CH446Q / WS2812: not driven by this skill.
- Real UART (`--port`): requires board + optional `pyserial`; not in Launch/Doctor/Drive helpers.
- JF2 device client / `host sw *` (M3+): not on `main` CLI surface yet when this skill was written — add features when those commands land.
- Eval scripts (`scripts/run_eval.py`, `run_scale_eval.py`, `run_plan_stats.py`): useful load tests; treat as optional CI-style checks, not primary user journeys in the map.
