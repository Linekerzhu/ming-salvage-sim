---
name: ccgs-code-review
description: "Architectural and quality code review on a specified Python module, route, React component, or test file in 大明王朝1628. Checks engineering-architecture.md compliance, SOLID, testability, FastAPI/SQLite/LLM-specific concerns. Adapted from CCGS code-review. Read-only — no files written."
argument-hint: "[path-to-file-or-directory]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, AskUserQuestion
---

# CCGS Code Review (Ming-adapted)

Adapted from Claude Code Game Studios `code-review`. The engine-specialist
phase (Godot/Unity/Unreal) is replaced by a stack-aware review that knows
this project's layers: FastAPI routes → web services → GameSession →
ming_sim mechanics → SQLite, plus the LLM audit pipeline and React front end.

This skill is **read-only** — it produces a review, never edits code.

---

## Phase 1: Load Target Files

Read the target file(s) in full. Read `docs/engineering-architecture.md` for
project layering rules (the authoritative architectural standard here, since
there is no formal ADR set).

---

## Phase 2: Architectural-Layer Classification

Classify the target file into a layer (this determines which checks apply):

| Layer | Where | Examples |
|-------|-------|----------|
| Web route shell | `web_app.py`, `*_api.py` | auth, request validation, response wrapping |
| Web service | `web/services/*` or service funcs | orchestration, payload contracts |
| Application | `session.py`, `timeflow.py` | GameSession, turn flow |
| Mechanics | most of `ming_sim/*.py` | treasury, faction, trust, petition, directive |
| Pipeline | `dialogue_audit.py`, `dialogue_semantics.py`, `*_probe.py` | LLM audit, portrait generation |
| Data/DB | `db.py`, `upgrade_schema.py` | SQLite schema, migrations |
| Frontend | `web/src/**/*.{ts,tsx}` | React components, api client |
| Test | `tests/test_*.py` | unittest |

---

## Phase 3: engineering-architecture.md Compliance

Check against the documented invariants:
- [ ] **Simulation state is not in the frontend** — game state lives in `ming_sim` + SQLite; UI only reads/displays
- [ ] **Web entry does not carry business rules** — `web_app.py` / routes do auth + validation + orchestration only
- [ ] **Pipelines have explicit contracts** — LLM/portrait/data-gen have input obj, output obj, failure state, testable boundary
- [ ] **Runtime deps stay light** — runtime reads JSON/SQLite/static only, no external NPC source libs or one-shot gen scripts
- [ ] **Admin routes are isolated** — whitelist tables, role permissions, audit logs, separate from game API
- [ ] **Performance budget is a contract** — first-screen payload, detail endpoints, build artifacts, LLM token caps all bounded by tests/probes

Flag any deviation as ARCHITECTURAL VIOLATION (blocking) / DRIFT (warning) / MINOR (info).

---

## Phase 4: Standards Compliance

- [ ] Public functions/classes have docstrings
- [ ] Cyclomatic complexity < ~10 per function
- [ ] No function > ~40 lines (excluding data declarations)
- [ ] Config values loaded from `content/` JSON, not hardcoded
- [ ] DB access parameterized (no f-string SQL)
- [ ] Routes declare Pydantic models, not bare dicts

---

## Phase 5: Architecture and SOLID

**Architecture:**
- [ ] Correct dependency direction: routes → services → mechanics → DB (never reverse)
- [ ] No circular imports between `ming_sim/` modules
- [ ] Proper layer separation (UI does not own game state)
- [ ] Cross-system communication via explicit function calls / events, not hidden global mutation
- [ ] Consistent with the 5-entry-point structure (御前/御案/召对/诏旨/国策)

**SOLID:**
- [ ] SRP — each module/function has one reason to change
- [ ] OCP — extendable without modification
- [ ] DIP — depends on abstractions where practical (DB access via `db.py`, not scattered `sqlite3.connect`)

---

## Phase 6: Stack-Specific Concerns

**FastAPI:**
- [ ] Write endpoints validate input (Pydantic, not `Request.json()`)
- [ ] Errors translated to safe responses (no stack-trace leakage)
- [ ] Route registered in `web_route_contracts.py` if it touches payload contract
- [ ] Idempotency where required (CAS gates on state mutation: `outcome_status='extracted'`, KV gates per turn/day)

**SQLite:**
- [ ] Parameterized queries (`?` placeholders)
- [ ] Indexes exist for hot WHERE/ORDER BY columns
- [ ] Transactions used for multi-statement state changes
- [ ] No schema drift between `upgrade_schema.py` and runtime queries

**LLM pipeline:**
- [ ] Player-authored strings are delimited/sanitized before entering prompts
- [ ] Audit results cached per turn where idempotent (the `_CONTEXT_CACHE` / `id(db)` keying)
- [ ] Failure path defined (LLM down → deterministic fallback, not crash)
- [ ] Token/cost bounded

**React/TS:**
- [ ] No `any` without justification; no blanket `@ts-ignore`
- [ ] State fetched via the api client, not scattered `fetch`
- [ ] Components memoized where they receive stable props
- [ ] No business logic in the component (simulation stays backend-side)

---

## Phase 7: Output Review

```
## Code Review: [File/System Name]

### Layer: [route / service / mechanics / pipeline / db / frontend / test]

### engineering-architecture.md Compliance: [COMPLIANT / DRIFT / VIOLATION]
[Findings with severity]

### Standards Compliance: [X/6 passing]
[Failures with line refs]

### Architecture: [CLEAN / MINOR / VIOLATIONS]
[Specific concerns]

### SOLID: [COMPLIANT / ISSUES]
[Specific violations]

### Stack-Specific Concerns
[FastAPI / SQLite / LLM / React findings]

### Positive Observations
[What is done well — always include]

### Required Changes
[Must-fix before approval — ARCHITECTURAL VIOLATIONs always here]

### Suggestions
[Nice-to-have]

### Verdict: [APPROVED / APPROVED WITH SUGGESTIONS / CHANGES REQUIRED]
```

Read-only — no files written.

---

## Phase 8: Next Steps

AskUserQuestion:
- Prompt: "Code review complete — verdict: [APPROVED / CHANGES REQUIRED]. How to proceed?"
- Options (adjust by verdict):
  - If APPROVED:
    - [A] Run `/ccgs-regression-suite update` to register coverage
    - [B] Stop here
  - If CHANGES REQUIRED:
    - [A] Fix the issues and re-run `/ccgs-code-review`
    - [B] Stop here

If an ARCHITECTURAL VIOLATION is found against `engineering-architecture.md`,
recommend fixing the implementation to comply (the doc is the source of truth;
if the design has genuinely changed, update the doc first, then the code).
