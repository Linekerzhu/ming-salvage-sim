---
name: ccgs-architecture-review
description: "Validate the 大明王朝1628 architecture for completeness and consistency. Builds a traceability matrix mapping the 5 entry points and 16 subsystems (architecture_inventory.md) to ming_sim modules and tests, detects cross-module conflicts (data ownership, state-authority, dependency direction), checks engineering-architecture.md compliance. Adapted from CCGS architecture-review. Produces PASS/CONCERNS/FAIL."
argument-hint: "[focus: full | coverage | consistency | layers | single-module module_name]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, AskUserQuestion
---

# CCGS Architecture Review (Ming-adapted)

Adapted from Claude Code Games Studios `architecture-review`. The GDD→ADR
traceability model is replaced by this project's actual architecture sources:
`architecture_inventory.md` (the 5 entry points + subsystem inventory) and
`docs/engineering-architecture.md` (the layering invariants). There is no ADR
set, so "ADR coverage" becomes "module-to-entry-point mapping + layer
compliance".

This is the architecture equivalent of a deep code review at the system level.

---

## Phase 1: Load Everything

### Phase 1a — Summary scan (fast)
Grep `## ` headers from the architecture docs to get the lay of the land without full reads:
```
Grep "^## " architecture_inventory.md
Grep "^## " docs/engineering-architecture.md
```

### Phase 1b — Full load
Read in full:
- `architecture_inventory.md` — the authoritative module/subsystem inventory + the 5-entry-point goal
- `docs/engineering-architecture.md` — layering rules, the 6 invariants, the mermaid layer diagram
- `docs/system-modules.md` if present
- `rebuildplan.md` / `docs/dev-roadmap.md` — intended direction (to detect drift)

Report: "Loaded architecture docs. [N] backend modules inventoried, [M] routes, [K] mobile views."

---

## Phase 2: Extract Technical Requirements (the "what the architecture must provide")

From `architecture_inventory.md` and `engineering-architecture.md`, extract the
required architectural properties. Categories:

| Category | Example in this project |
|----------|-------------------------|
| Entry-point structure | Exactly 5 first-class entries: 御前/御案/召对/诏旨/国策; everything else hangs under one |
| State authority | Simulation state in ming_sim+SQLite, never frontend |
| Layer direction | routes → services → session → mechanics → DB; never reverse |
| Pipeline contracts | LLM/portrait/data-gen have input/output/failure/testable-boundary |
| Admin isolation | Admin routes on whitelist+role+audit, separate from game API |
| Perf budgets | First-screen payload, detail endpoints, build, LLM token cap all bounded |
| Idempotency | State mutations guarded by CAS/KV gates (extracted outcomes, per-turn inertia, daily eunuch tick) |
| Save compat | Schema migrations via `upgrade_schema.py`, archived state stays loadable |

Each becomes a requirement `TR-[domain]-NNN`. This is the requirements baseline.

---

## Phase 3: Build the Traceability Matrix

For each requirement, verify it is actually satisfied by code:

1. Grep `ming_sim/*.py` and `web_app.py`/`*_api.py` for the implementing module(s)
2. Grep `tests/test_*.py` for a test that guards the invariant
3. Mark coverage:

| Status | Meaning |
|--------|---------|
| ✅ **Covered** | Implementing module + test exist |
| ⚠️ **Partial** | Implemented but no/weak test, or test covers happy path only |
| ❌ **Gap** | Requirement stated but no implementing module, or module exists with no parent entry point |

Build the matrix:
```
| Requirement | Entry point / subsystem | Implementing module(s) | Test(s) | Status |
```

Special focus: the **no-parent modules** — any `ming_sim/*.py` that doesn't
clearly hang under one of the 5 entry points is a Gap (this is the project's
documented #1 architectural risk).

---

## Phase 4: Cross-Module Conflict Detection

Compare modules for contradictions. A conflict exists when:

- **State-authority conflict**: two modules both claim to mutate the same state (treasury, trust, faction power, grievance) without a single owner / CAS gate
- **Layer-direction violation**: a mechanics module imports a route/web module; DB layer imports mechanics
- **Idempotency conflict**: a state mutation lacks the CAS/KV gate its sibling mutations have (inconsistent double-application protection — the historical bug class here)
- **Dependency cycle**: circular imports between `ming_sim/` modules
- **Cache-key conflict**: a memoization cache keyed too coarsely (e.g. `(name, turn)` without `id(db)`) causing cross-DB/turn leakage
- **Schema drift**: runtime queries reference columns/tables not created by `upgrade_schema.py`, or vice versa

For each conflict:
```
## Conflict: [moduleA] vs [moduleB]
Type: [State authority / Layer direction / Idempotency / Cycle / Cache key / Schema]
moduleA claims: ...
moduleB claims: ...
Impact: ...
Resolution options: ...
```

### Dependency ordering
Build the import graph across `ming_sim/` modules. Topologically sort. Flag
cycles and modules that import something not yet initialized at their call time.

---

## Phase 5: Layer Compliance (engineering-architecture.md)

Across the codebase verify the 6 invariants:
- [ ] Simulation state not in frontend (grep `web/src` for direct game-state mutation — should be none)
- [ ] Web entry carries no business rules (routes do auth/validation/orchestration only)
- [ ] Pipelines have explicit contracts (input/output/failure objects)
- [ ] Runtime deps light (no external NPC libs / one-shot gen scripts at runtime)
- [ ] Admin routes isolated (whitelist + role + audit log)
- [ ] Perf budgets are contracts (tests/probes bound them)

Output:
```
### Layer Compliance Results
Invariants satisfied: X / 6
[layer-by-layer findings]
```

---

## Phase 6: Coverage Gaps (orphaned / no-parent modules)

List every `ming_sim/*.py` module and assign it to one of the 5 entry points
(or mark ORPHAN). Per `architecture_inventory.md`, the strategic goal is that
**no module is orphaned**. This is the single most important output of the
review for this project.

```
### Module → Entry Point Mapping
| Module | Assigned entry point | Status |
|--------|---------------------|--------|
| ... | 御前 audience | MAPPED |
| ... | (none) | ORPHAN ⚠ |
```

---

## Phase 7: Output the Review Report

```
## Architecture Review Report — 大明王朝1628
Date: [date]
Docs reviewed: [list]
Modules inventoried: [N]

### Traceability Summary
Total requirements: [N]
✅ Covered: [X]  ⚠️ Partial: [Y]  ❌ Gaps: [Z]

### Coverage Gaps
[each gap with suggested action]

### Cross-Module Conflicts
[from Phase 4]

### Layer Compliance
[X/6 invariants satisfied]

### Orphaned Modules (no parent entry point)
[from Phase 6 — the priority list]

### Verdict: [PASS / CONCERNS / FAIL]
PASS: all requirements covered, no conflicts, all modules mapped, 6/6 invariants
CONCERNS: some gaps/partial/orphans, but no blocking conflicts
FAIL: blocking conflict (state-authority / cycle / schema drift) or >25% modules orphaned
```

---

## Phase 8: Write Output

AskUserQuestion:
- "Review complete. What to write?"
  - [A] Write report to `docs/architecture-review-[date].md`
  - [B] Also update `architecture_inventory.md` module→entry-point mapping
  - [C] Don't write — review findings first

---

## Collaborative Protocol
1. **Read silently** — don't narrate every file.
2. **Show the matrix** — present traceability + orphan map before asking anything.
3. **Don't guess** — if a module's parent entry point is ambiguous, ask.
4. **Draft before approval** — show what will be written inline first.
5. **Use AskUserQuestion for write approvals** — not plain "may I?".
6. **Non-blocking** — verdict is advisory; user decides whether to continue.
7. **The orphan-map is the headline output** — for this project, module consolidation toward 5 entry points is the strategic ask.
