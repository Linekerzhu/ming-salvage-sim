---
name: ccgs-tech-debt
description: "Track, categorize, and prioritize technical debt across the 大明王朝1628 codebase. Scans ming_sim/, web/src/, and tests/ for debt indicators, maintains a debt register at docs/tech-debt-register.md, and recommends repayment scheduling. Adapted from CCGS tech-debt for this Python/FastAPI/React stack."
argument-hint: "[scan | add | prioritize | report]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, AskUserQuestion
---

# CCGS Tech Debt (Ming-adapted)

Adapted from Claude Code Game Studios `tech-debt`. The scan patterns target
this project's real debt surfaces: the 89+ `ming_sim/*.py` modules, the
React/TS front end, the LLM audit pipeline, and the test suite.

Per `architecture_inventory.md`, this project's known systemic risk is
**too many modules with no parent** (74+ backend modules, 121 routes). Tech
debt here is often *architectural consolidation debt*, not just code smells.

---

## Phase 1: Parse Subcommand

- `scan` — scan the codebase for debt indicators
- `add` — add a new debt entry manually
- `prioritize` — re-prioritize the existing register
- `report` — summary of current debt status (read-only)

If no subcommand: output usage and stop. Verdict: **FAIL — missing subcommand**.

---

## Phase 2A: Scan Mode

Search the codebase for debt indicators:

**Code-level:**
- `TODO` / `FIXME` / `HACK` / `XXX` comments (count + categorize)
- `# type: ignore`, `@ts-ignore`, `eslint-disable` (suppressed type/lint errors)
- `deprecated` markers
- Files over 500 lines (potential god modules — check the largest `ming_sim/*.py`)
- Functions over 50 lines (complexity)
- Duplicated logic blocks (same pattern across modules)

**Architecture-level (this project's primary debt class):**
- Modules with no clear parent among the 5 entry points (per `architecture_inventory.md`)
- Routes not registered in `web_route_contracts.py` payloads/exclusions
- Dead endpoints returning 410 Gone with no removal plan (e.g. migrated quest endpoints)
- Cross-cutting concerns duplicated per subsystem instead of shared (KV gate pattern, cache invalidation)
- LLM audit calls still serial where they could be merged

**Test debt:**
- `ming_sim/` modules with no `tests/test_*` counterpart
- Tests skipped (`@unittest.skip`, `pytest.mark.skip`)
- Assertion-free tests (`def test_*` with no `assert`)

**Dependency debt:**
- Unbounded versions in `requirements.txt` / `pyproject.toml`
- Outdated pins

Categorize each finding:
- **Architecture Debt** — wrong abstractions, missing patterns, coupling, no-parent modules
- **Code Quality Debt** — duplication, complexity, naming, missing types, suppressed lints
- **Test Debt** — missing tests, skips, untested invariants
- **Documentation Debt** — missing/outdated docs (compare `architecture_inventory.md` counts vs reality)
- **Dependency Debt** — outdated/unbounded packages
- **Performance Debt** — known slow paths, N+1 queries, serial LLM calls

Present findings. Ask: "May I write these to `docs/tech-debt-register.md`?"
If yes: update (append, do not overwrite). Verdict: **COMPLETE**.
If no: stop. Verdict: **BLOCKED**.

---

## Phase 2B: Add Mode

Ask for description, affected files, impact if unfixed. Then via AskUserQuestion collect **category** (the 6 above) and **effort** (S <1d / M 1–3d / L 3–7d / XL >1w).

Present the entry. Ask: "May I append to `docs/tech-debt-register.md`?"
Verdict: **COMPLETE** or **BLOCKED**.

---

## Phase 2C: Prioritize Mode

Read `docs/tech-debt-register.md`. Score each item by:
`(impact_if_unfixed × frequency_of_encounter) / fix_effort`

Re-sort and recommend what to address next. Note: architecture-debt items that
block the "compress to 5 entry points" goal from `architecture_inventory.md`
should be weighted up regardless of raw score.

Present. Ask to write back. Verdict: **COMPLETE** or **BLOCKED**.

---

## Phase 2D: Report Mode

Read the register. Summarize: items by category, total effort, added vs resolved since last, trend. Flag items older than ~1 month of active dev without action. Read-only. Verdict: **COMPLETE**.

---

## Phase 3: Next Steps
- High-priority architecture debt → feed into the consolidation plan toward 5 entry points
- Run `/ccgs-tech-debt report` at the start of each work cycle to track trends

### Debt Register Format
```markdown
## Technical Debt Register
Last updated: [date]
Total items: [N] | Estimated total effort: [summed T-shirts]

| ID | Category | Description | Files | Effort | Impact | Priority | Added |
|----|----------|-------------|-------|--------|--------|----------|-------|
| TD-001 | [Cat] | [Desc] | [files] | [S/M/L/XL] | [Low/Med/High/Critical] | [Score] | [date] |
```

### Rules
- Tech debt is a tool, not a sin — the register tracks conscious decisions.
- Every entry must explain WHY it was accepted (deadline, prototype, missing info).
- Run `scan` at least once per work cycle.
- Architecture debt toward the 5-entry-point goal is the strategic priority for this project.
- Items older than ~1 month without action should be fixed or consciously accepted with a documented reason.
