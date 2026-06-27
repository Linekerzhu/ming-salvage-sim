---
name: ccgs-perf-profile
description: "Structured performance profiling for 大明王朝1628. Identifies hot paths in the FastAPI backend, the per-chat-turn LLM audit pipeline, SQLite query load, and React bundle/render. Adapted from CCGS perf-profile for this Python/SQLite/React stack. Produces a prioritized report."
argument-hint: "[system-name | backend | llm | frontend | full]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash
---

# CCGS Performance Profile (Ming-adapted)

Adapted from Claude Code Game Studios `perf-profile`. The engine-specific
budgets (FPS, draw calls, frame time) have been replaced by this project's
real budgets: per-chat-turn latency, LLM call count per turn, SQLite query
count per turn, and React bundle size / first-render.

This skill is **static analysis** — it identifies candidates by reading code.
Runtime profiling (cProfile, `EXPLAIN QUERY PLAN`, React profiler) confirms.

---

## Phase 1: Determine Scope

Read the argument:
- `backend` → FastAPI routes, service layer, DB access in `ming_sim/*.py`
- `llm` → the per-chat-turn audit pipeline (`dialogue_audit.py`, `dialogue_semantics.py`, agents)
- `frontend` → React/TS bundle and render (`web/src/**`)
- A specific module name → focus on that module
- `full` or no argument → all of the above

---

## Phase 2: Load Performance Budgets

Read `docs/engineering-architecture.md` §性能预算是接口契约 and any
`docs/design-upgrade-semi-realtime.md` for stated targets. If none stated,
use these defaults:

| Metric | Default Budget |
|--------|----------------|
| Per-chat-turn wall time | < 3s (LLM-bound) |
| LLM calls per chat turn | ≤ 2 (after the merged-audit optimization) |
| SQLite queries per chat turn | < 20 |
| First-screen state payload | < 200KB JSON |
| Vite production build | < 30s, bundle < 1.5MB gzipped |
| Endpoint p95 (non-LLM) | < 200ms |

Note which budgets are explicitly documented vs. assumed.

---

## Phase 3: Analyze Codebase

### Backend CPU / DB targets (`ming_sim/*.py`)
- Functions called once per chat turn or once per tick (`_tick_day`, `tick_directives`, `evaluate_user_message`, `evaluate_post_chat`) — estimate cost.
- Repeated DB queries inside loops (N+1): `for ... db.execute(...)`.
- `_context_payload` and any other per-turn context rebuilders — count queries.
- Missing indexes: `WHERE`/`ORDER BY` columns not covered by an index (check `upgrade_schema.py` CREATE INDEX list).
- Memoization present but keyed wrong (e.g. keyed only on `(name, turn)` not `id(db)` — cross-DB leak / stale).
- String building in hot paths (large f-strings / joins per turn).

Grep: `for .* in .*:`, nested `db.execute`, `db.fetchall()`, `def _context_payload`, `@functools.cache`, `lru_cache`.

### LLM pipeline targets
- Count of serial LLM calls per chat turn (pre-optimization was 7–10; merged audits reduced this).
- Redundant context sent to each call (full payload rebuilt per call instead of reused).
- No caching of idempotent audit results across the same turn.
- Token cost: oversized system prompts, full history re-sent each turn.

Grep: `audit_client`, `agent.run`, `.run(`, `system_prompt=`, `_CONTEXT_CACHE`, `dialogue_combined_`.

### Frontend targets (`web/src/**`)
- Bundle: large deps imported whole (moment, lodash, full icon sets).
- Render: components re-rendering on every state change without memoization; inline object/array props.
- Data: over-fetching (loading full lists when a page needs a slice); missing `npc=` filter style params.
- Vite config: source maps in prod, no code-splitting.

Grep: `import .* from "lodash"`, `import .* moment`, `useEffect`, `useMemo`, `React.memo`, check `web/vite.config.*`.

### Memory / resource targets
- Large dicts held for the whole process lifetime (module-level caches without eviction).
- File handles or DB connections not returned to a pool.
- Portrait/asset buffers held in memory.

---

## Phase 4: Generate Profiling Report

```markdown
## Performance Profile: [System or Full]
Generated: [date]

### Performance Budgets
| Metric | Budget | Estimated Current | Status |
|--------|--------|-------------------|--------|
| Per-chat-turn wall | 3s | [est] | OK/WARN/OVER |
| LLM calls/turn | 2 | [est] | |
| SQLite queries/turn | 20 | [est] | |
| First-screen payload | 200KB | [est] | |
| Build time | 30s | [est] | |

### Hotspots Identified
| # | Location | Issue | Estimated Impact | Fix Effort |
|---|----------|-------|------------------|------------|

### Optimization Recommendations (Priority Order)
1. **[Title]** — [Description]
   - Location: [file:line]
   - Expected gain: [estimate]
   - Risk: [Low/Med/High]
   - Approach: [How to implement]

### Quick Wins (< 1 hour each)
- ...

### Requires Investigation (needs runtime profiling)
- ...
```

Summarize: top 3 hotspots, headroom vs budget, recommended next action.

---

## Phase 5: Scope and Timeline Decision

Activate only if any hotspot has Fix Effort M or L. Present each and ask:
- **A) Implement** now or schedule
- **B) Reduce scope** (drop the feature driving the cost)
- **C) Accept and defer** (log as known issue)
- **D) Escalate** for an architectural decision (e.g. restructure the audit pipeline)

If items are deferred, record under `### Deferred`.

This skill is read-only — no files are written. Verdict: **COMPLETE**.

---

## Rules
- Never optimize without measuring first — gut feelings are unreliable. Static analysis identifies candidates; runtime profiling confirms.
- Recommendations must include estimated impact — "make it faster" is not actionable.
- The LLM audit pipeline is the single biggest perf lever in this project — always profile it for `llm` and `full` modes.
