---
name: ccgs-smoke-check
description: "Run the critical-path smoke gate for 大明王朝1628 before release. Runs the unittest suite (pytest runner), verifies the 5 entry points and core state invariants, checks tsc/build, and produces a PASS/FAIL report. Adapted from CCGS smoke-check. A failed smoke check means not ready to ship."
argument-hint: "[quick | full | --layer backend|frontend|all]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Write, AskUserQuestion
---

# CCGS Smoke Check (Ming-adapted)

Adapted from Claude Code Game Studios `smoke-check`. The engine test runners
(Godot headless, Unity editor, UE automation) are replaced by this project's
actual test command: the `tests/` unittest suite run via the `.venv` pytest
runner, plus a TypeScript/Vite build check.

The rule: **a build that fails smoke check does not ship.**

**Output:** `docs/qa/smoke-[date].md`

---

## Parse Arguments

**Base mode** (default `full`):
- `quick` — skip coverage scan (Phase 3) and Batch 3; for rapid re-checks
- `full` — complete check

**Layer flag** (`--layer`, default `all`):
- `--layer backend` — Python suite + DB schema check only
- `--layer frontend` — tsc + Vite build only
- `--layer all` — both, plus a per-layer verdict table

---

## Phase 1: Detect Environment

1. **Test dir**: confirm `tests/` exists and `tests/test_*.py` is non-empty. If absent: "No tests/ — cannot smoke check." Stop.
2. **Runner**: confirm `.venv/bin/python -m pytest --version` works. Note pytest version.
3. **Frontend**: confirm `web/package.json` + `web/tsconfig.json` exist. Note node version.
4. **Smoke list**: check `docs/qa/smoke-tests.md` or `tests/regression-suite.md` for a curated critical-path list; load if present.
5. **DB schema**: note `ming_sim/upgrade_schema.py` presence (schema-integrity check uses it).

Report: "Env: Python [ver], pytest [ver], node [ver]. Tests: [N files]. Frontend: [present/absent]."

---

## Phase 2: Run Automated Tests

**Backend** (run unless `--layer frontend`):
```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -40
```
Parse: total run, passed, failed, errors. Capture up to 10 failing test names + their tracebacks' top line.

**Frontend** (run unless `--layer backend`):
```bash
cd web && npx tsc --noEmit 2>&1 | tail -20
```
If tsc clean, optionally:
```bash
cd web && npx vite build 2>&1 | tail -20
```
(timed; note duration. A cold-cache one-off timeout is not a FAIL — re-run once.)

If a runner is unavailable, report NOT RUN with reason — **do not treat NOT RUN as automatic FAIL**; record as a warning for manual confirmation.

---

## Phase 3: Check Test Coverage (skip if `quick`)

For each of the 5 entry points and the core state-invariant areas, confirm a test exists:

| Area | Look for test file containing |
|------|-------------------------------|
| 御前 audience | `test_*audience*`, `test_attendant*` |
| 御案 desk | `test_desk*` |
| 召对 dialogue | `test_dialogue*` |
| 诏旨 decree/directive | `test_*decree*`, `test_*directive*`, `test_lifecycle*` |
| 国策 policy/edict | `test_*edict*`, `test_*policy*` |
| Treasury invariant | `test_*treasury*`, `test_finance*` |
| Trust/grievance | `test_*trust*`, `test_intrigue*` |
| Petition lifecycle | `test_*petition*` |
| Assignment hall | `test_assignment*` |

Assign per area: **COVERED** / **MISSING** / **EXPECTED** (config/data). MISSING is advisory (does not FAIL) but must appear prominently.

---

## Phase 4: Manual Smoke Checks

Use AskUserQuestion, ≤3 calls. Tailor to actual systems.

**Batch 1 — Core stability (always):**
```
question: "Core stability — select any FAILED (leave unselected if all passed):"
multiSelect: true
options:
  - "Backend won't start / crashes on boot (uvicorn)"
  - "New game / session fails to start"
  - "DB schema upgrade fails on a fresh DB"
  - "Crash or hang during a basic turn tick"
```

**Batch 2 — Entry points + regression:**
```
question: "Entry points & regression — select any FAILED:"
multiSelect: true
options:
  - "御前 audience — broken"
  - "召对 dialogue / LLM audit — broken or LLM hard-fails"
  - "诏旨 decree → directive lifecycle — broken"
  - "Assignment hall — broken"
  - "Regression in a previously-fixed area — broken"
```

**Batch 3 — Data integrity + perf (skip if `quick`):**
```
question: "Data integrity & perf — select any FAILED/skipped:"
multiSelect: true
options:
  - "Save/load — data loss or corruption"
  - "State delta applied twice (treasury/trust/faction) — the known bug class"
  - "Per-chat-turn latency or LLM call count regressed"
  - "Not checked this session"
```

Record responses verbatim.

---

## Phase 5: Generate Report

````markdown
## Smoke Check Report — 大明王朝1628
Date: [date]
Mode: [full | quick]  Layer: [backend|frontend|all]
Python [ver] / pytest [ver] / node [ver]

### Automated Tests
**Backend**: [PASS (N tests, N passing) | FAIL (N failures) | NOT RUN (reason)]
[if FAIL, list failing tests + 1-line cause]
**Frontend tsc**: [PASS | FAIL (N errors)]
**Frontend build**: [PASS (Ns) | FAIL | skipped]

### Test Coverage (full mode)
| Area | Test File | Status |
**Summary**: [N] covered, [N] missing, [N] expected.

### Manual Smoke Checks
- [x] Backend boots — PASS
- [x] New game starts — PASS
- [ ] [area] — FAIL: [user desc]

### Missing Test Evidence
[areas with no test — must resolve before release]

### Per-Layer Verdict (--layer all only)
| Layer | Checks | Passed | Failed | Verdict |

### Verdict: [PASS | PASS WITH WARNINGS | FAIL]
**FAIL** if any: backend suite has failures, OR any Batch 1/2 check FAIL, OR tsc/build FAIL
**PASS WITH WARNINGS** if: tests PASS/NOT-RUN-unconfirmed, all manual checks PASS, but MISSING coverage exists
**PASS** if: all tests PASS, all checks PASS, no MISSING
````

---

## Phase 6: Write and Gate

Present full report. Ask: "May I write to `docs/qa/smoke-[date].md`?"
Write only after approval.

**If FAIL:**
"Do not ship until resolved: [list each failure]. Fix and re-run `/ccgs-smoke-check`."

**If PASS WITH WARNINGS:**
"Ready to ship with advisory gaps: [MISSING items]. Resolve before final release."

**If PASS:**
"Smoke check passed cleanly. Ready to ship."

---

## Collaborative Protocol
- **Never treat NOT RUN as automatic FAIL** — record NOT RUN, let the dev confirm. Unconfirmed NOT RUN → PASS WITH WARNINGS.
- **Never auto-fix failures** — report and state what must be resolved. Do not edit source/test files.
- **PASS WITH WARNINGS does not block** — records advisory gaps.
- **`quick` skips Phase 3 and Batch 3** — for rapid re-checks after a specific fix.
- **The double-application bug class is explicitly in Batch 3** — it has recurred historically; always ask about it in full mode.
- **Ask before writing** — Phase 6 requires approval.
