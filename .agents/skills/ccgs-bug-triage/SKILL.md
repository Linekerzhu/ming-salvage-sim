---
name: ccgs-bug-triage
description: "Process the open bug backlog for 大明王朝1628 into a prioritized action list. Reads bugs from docs/bugs/, commit history, and test failures; classifies severity vs priority; detects systemic trends (e.g. repeated double-application bugs in state mutations); produces a triage report. Adapted from CCGS bug-triage."
argument-hint: "[sprint | full | trend]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, AskUserQuestion
---

# CCGS Bug Triage (Ming-adapted)

Adapted from Claude Code Game Studios `bug-triage`. The bug sources are this
project's actuals: `docs/bugs/*.md` if used, recent `Fix:`/`fix:` commits,
known historical bug classes, and failing tests. Sprint context comes from
`docs/dev-roadmap.md` / `rebuildplan.md`.

This project has a documented historical bug pattern: **double-application of
state deltas** (petition settle, intrigue coerce, session drain, issues
inertia, eunuch tick) — trend analysis should specifically watch for it.

**Output:** `docs/bugs/bug-triage-[date].md`

**When to run:**
- Start of a work cycle — assign open bugs
- After a batch of fixes — confirm none regressed
- When open bugs cross ~10

---

## 1. Parse Arguments

- `sprint` — triage against current cycle scope
- `full` — all bugs regardless of scope
- `trend` — trend analysis only (read-only)
- No argument — `sprint` if a current cycle is evident from roadmap, else `full`

---

## 2. Load Bug Backlog

### Step 2a — Discover bugs, in priority order
1. `docs/bugs/*.md` — individual bug files (preferred)
2. `docs/bugs.md` — consolidated log (fallback)
3. Recent commits: `git log --oneline -60 | grep -iE "fix|bug"` (last resort)
4. Currently-failing tests: `.venv/bin/python -m pytest tests/ -q 2>&1 | tail` (if any)

If none found: "No open bugs detected. Nothing to triage." Stop.

### Step 2b — Load cycle context
Read `docs/dev-roadmap.md` / `rebuildplan.md` for current focus. Note the
active work theme (e.g. "assignment hall", "dialogue perf", "quest→petition
migration") so bugs can be assigned against it.

### Step 2c — Load severity reference
Use the standard definitions below unless `docs/engineering-architecture.md`
overrides.

---

## 3. Classify Each Bug

### Severity (impact)
| Severity | Definition |
|----------|-----------|
| **S1 Critical** | Crash, data corruption/loss, broken save, LLM pipeline hard-fail. Cannot proceed. |
| **S2 High** | Major feature broken but app still runs. Wrong state delta, broken route. |
| **S3 Medium** | Feature degraded, workaround exists. Minor wrong behavior. |
| **S4 Low** | Cosmetic, typo, log noise. No gameplay/state impact. |

### Priority (urgency)
| Priority | Definition |
|----------|-----------|
| **P1 Fix this cycle** | Blocks release/demo, or is a regression |
| **P2 Fix soon** | Before next milestone |
| **P3 Backlog** | No active blocking impact |
| **P4 Won't fix / Deferred** | Accepted risk / out of scope |

### Assignment
For P1/P2 in `sprint` mode: map to the responsible `ming_sim/` module and the
entry point it serves; check cycle capacity; assign or flag overflow.

### Deviation / systemic check
- 3+ bugs in the same module this cycle → "Quality issue in [module]"
- 2+ S1/S2 in the same area → "Area may need re-review before shipping"
- **Bug filed against a previously-fixed area** → "Regression — re-open and add regression test"
- **Double-application pattern** → any bug where a state delta (treasury/trust/faction/grievance) applied twice is a known historical class; flag as SYSTEMIC and require a CAS/KV gate fix + regression test, not just a patch

---

## 4. Trend Analysis

- Volume: total open, opened this cycle, closed this cycle, net change
- Hot spot: module with most open bugs
- Age: bugs older than ~1 month
- Regressions: bugs against previously-fixed areas
- **Systemic pattern watch**: count of double-application/state-delta bugs (the project's recurring class)

---

## 5. Generate Triage Report

```markdown
# Bug Triage Report — 大明王朝1628
Date: [date]
Mode: [sprint | full | trend]
Open bugs processed: [N]
Cycle scope: [theme or "N/A"]

## Triage Summary
| Priority | Count | Notes |
| P1 — this cycle | [N] | [N] assigned, [N] overflow |
| P2 — soon | [N] | next milestone |
| P3 — backlog | [N] | deferred |
| P4 — won't fix | [N] | accepted risk |

**Critical (S1/S2) unfixed**: [N]

## P1 Bugs — This Cycle
| ID/commit | Module | Severity | Summary | Entry point |

## P2 / P3 / P4
[same format]

## Systemic Issues Flagged
[double-application patterns, module hot spots, regressions — or "None"]

## Trend Analysis
Volume / hot spot / regressions / aged / systemic-pattern count

[If aged S1/S2 > 0:]
> ⚠️ [N] high-severity bugs open >1 month without assignment — accepted risk, review explicitly.

## Recommended Actions
1. [most urgent]
2. [investigate hot spot]
3. [add regression tests for systemic class]
```

---

## 6. Write and Gate

Present report. Ask: "May I write to `docs/bugs/bug-triage-[date].md`?"
Write only after approval.

After writing:
- Unassigned S1 → "S1 bugs must be assigned before the cycle is healthy."
- Regressions → "Regressions found — re-open affected areas and run `/ccgs-regression-suite update`."
- Double-application systemic flag → "This is a recurring pattern. Require a CAS/KV gate + regression test for each, not a patch alone."
- No P1 → "No P1 bugs — build is in good shape."

Verdict: **COMPLETE** (or **BLOCKED** if declined).

---

## Collaborative Protocol
- **Never close/Won't-Fix without approval** — surface as P4 candidates and ask.
- **Never auto-assign to a cycle at capacity** — flag overflow, let the owner decide.
- **Severity is objective; priority is a decision** — present severity as recommendation.
- **The double-application class is first-class here** — it has recurred across petition/intrigue/session/issues/eunuch; treat any new instance as systemic, not incidental.
