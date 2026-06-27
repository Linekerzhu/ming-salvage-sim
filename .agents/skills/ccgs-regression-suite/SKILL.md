---
name: ccgs-regression-suite
description: "Map test coverage in tests/ to critical paths in ming_sim/, identify fixed bugs without regression tests, flag coverage drift, and maintain tests/regression-suite.md. Adapted from CCGS regression-suite for this Python/FastAPI project. Run after a bug fix or before a release."
argument-hint: "[update | audit | report]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion
---

# CCGS Regression Suite (Ming-adapted)

Adapted from Claude Code Game Studios `regression-suite`. The game-design
terms (GDD acceptance criteria, story files) map to this project's real
sources of truth: `ming_sim/*.py` modules, `docs/engineering-architecture.md`,
`architecture_inventory.md`, and the 5 primary entry points (御前/御案/召对/诏旨/国策).

A regression suite is **a curated list of tests already in `tests/`** that
collectively cover the game's critical paths and known failure points. This
skill maintains that list.

**Output:** `tests/regression-suite.md`

**When to run:**
- After fixing a bug (confirm a regression test exists or identify the gap)
- Before a release
- At sprint boundary to detect coverage drift

---

## 1. Parse Arguments

**Modes:**
- `update` — scan recent bug fixes (recent commits) and check for regression test presence; add new tests to the manifest
- `audit` — full audit of all critical paths vs. existing test coverage; flag paths with no regression test
- `report` — read-only status report (no writes)
- No argument — ask which mode via AskUserQuestion

---

## 2. Load Context

### Step 2a — Load existing regression suite
Read `tests/regression-suite.md` if it exists. Extract: total registered tests, last updated, any STALE/QUARANTINED entries. If absent: "No regression suite found — will create one."

### Step 2b — Load test inventory
Glob:
```
tests/test_*.py
```
For each file note the system (from filename: `test_dialogue_*` → dialogue, `test_assignment*` → assignment-hall, etc.). Do not read contents unless needed for mapping.

### Step 2c — Load critical paths
For `audit` mode: read `architecture_inventory.md` (lists the 5 entry points + 16 subsystems) and `docs/engineering-architecture.md`. Extract the critical paths:
- The 5 primary entry points (御前 audience / 御案 desk / 召对 / 诏旨 decree / 国策 policy) each must have end-to-end tests
- Core mechanics with correctness invariants: treasury, faction power, trust/grievance, petition lifecycle, directive lifecycle, assignment hall
- Known recent bugs (from the audit history: petition settle-on-done, intrigue double-coerce, session CAS drain, issues inertia double-drift, eunuch daily double-adjust)

For `update` mode: read recent commits (`git log --oneline -30`) for "Fix ..."/"fix ..." commits; identify the module each touched.

### Step 2d — Load closed bugs
Glob `docs/bugs/*.md`, `docs/qa/*.md`, or scan commit messages for `Fix:` / `fix:`. Note module + whether a test was added in the same commit.

---

## 3. Map Coverage — Critical Paths (audit mode)

For each critical path, determine whether a test exists:

1. Grep `tests/test_*.py` for the relevant module/function noun
2. Assign:

| Status | Meaning |
|--------|---------|
| **COVERED** | A test file exists that targets this path's logic |
| **PARTIAL** | A test exists but doesn't cover all cases (happy path only) |
| **MISSING** | No test found for this critical path |
| **EXEMPT** | Pure UI/visual path — covered by manual check, not automatable |

3. Elevate MISSING items that protect state invariants (treasury, trust, petition/directive lifecycle) to **HIGH PRIORITY** — these are the most likely regression sources and the ones with historical bugs.

---

## 4. Map Coverage — Fixed Bugs

For each bug fix (from commits or bug docs):

1. Extract the module
2. Grep `tests/` for a test referencing the bug scenario or the fixed function
3. Assign:
   - **HAS REGRESSION TEST** — a test guards against recurrence
   - **MISSING REGRESSION TEST** — fixed without a guard

For MISSING items, suggest the test file path: `tests/test_[module]_regression.py` and note: "Without this test, this bug can silently return."

---

## 5. Detect Coverage Drift

- Modules in `ming_sim/` with no corresponding `tests/test_*` file
- New subsystems added since the last `regression-suite.md` update
- `regression-suite.md` last-updated date vs. current — if gap > ~1 month of active dev, likely stale

---

## 6. Generate Report and Suite Manifest

Report format (in conversation):

```
## Regression Suite Status
Mode: [update | audit | report]
Existing registered tests: [N]
Test files scanned: [N]

### Critical Path Coverage (audit mode)
| Entry point / subsystem | Covered | Partial | Missing |
|--------------------------|---------|---------|---------|

### Bug Regression Coverage
| Bug (commit/module) | Has Regression Test? |
|---------------------|----------------------|

### Coverage Drift
[list, or "None detected"]

### Recommended New Regression Tests
| Priority | Module | Suggested Test File | Covers |
```

Suite manifest (`tests/regression-suite.md`):

```markdown
# Regression Suite Manifest
> Last Updated: [date]
> Total registered tests: [N]
> Coverage: [N]% of critical paths

## How to run
cd <repo> && .venv/bin/python -m pytest tests/    # or: .venv/bin/python -m unittest discover -s tests

## Registered Regression Tests
### [Module]
| Test File | Covers | Added |

## Known Gaps
| Priority | Module | Suggested Path | Covers | Reason |

## Quarantined Tests
| Test File | Reason | Since |
```

---

## 7. Write Output

Ask: "May I write/update `tests/regression-suite.md`?"
- `update`: append new entries; never remove existing (use Edit with targeted insertions)
- `audit`: rewrite the full manifest
- `report`: do not write

After writing:
- For each HIGH gap: "Consider creating the missing regression test before the next release."
- If bug regression gaps > 0: "These bugs can silently return without tests."
- If drift detected: "Re-run `/ccgs-regression-suite audit` at the next boundary."

Verdict: **COMPLETE** (or **BLOCKED** if user declined write).

---

## Collaborative Protocol
- **Never remove existing entries** without explicit approval — removing a deliberately-written test is itself a regression risk.
- **Gaps are advisory, not blocking** — surface clearly but do not block other work (except at release).
- **Quarantine is not deletion** — flaky tests are noted, not removed.
- **Ask before writing** — confirm before creating/updating the manifest.
- **State-invariant tests are the priority** — treasury/trust/petition/directive correctness has a documented history of double-application bugs; these tests matter most.
