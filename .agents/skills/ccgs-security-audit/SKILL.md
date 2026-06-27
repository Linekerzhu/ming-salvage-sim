---
name: ccgs-security-audit
description: "Audit the 大明王朝1628 codebase for security vulnerabilities: SQLite injection, save/state tampering, LLM prompt injection, FastAPI auth/authorization gaps, secret exposure, and input validation. Adapted from CCGS security-audit for this Python/FastAPI/SQLite/React stack. Produces a prioritized report in docs/security/."
argument-hint: "[full | db | llm | input | api | quick]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Write, AskUserQuestion
---

# CCGS Security Audit (Ming-adapted)

Adapted from Claude Code Game Studios `security-audit`. The game-engine
categories (save tampering, network packets, anti-cheat) have been rewritten
for this project's actual attack surfaces: a FastAPI backend, a SQLite game
DB, an LLM (agno) audit pipeline, and a React/TS SPA front end.

**Run this skill:**
- Before any public-facing deploy or demo
- After implementing any endpoint that writes to the DB, runs SQL, or calls the LLM
- When a security-related bug is reported

**Output:** `docs/security/security-audit-[date].md`

---

## Phase 1: Parse Arguments and Scope

**Modes:**
- `full` — all categories (recommended before release)
- `db` — SQLite/SQL only
- `llm` — LLM prompt-injection / token-leak / cost only
- `input` — input validation and injection only
- `api` — FastAPI auth/authorization/route-contract only
- `quick` — high-severity checks only (fastest, for iterative use)
- No argument — run `full`

Confirm the stack by reading `requirements.txt` / `pyproject.toml` for:
`fastapi`, `agno`, `pydantic`, `uvicorn`, `httpx`. Note LLM provider in use.

---

## Phase 2: Audit Categories

Run each applicable category. Grep patterns below target this codebase's
real file layout (`ming_sim/*.py`, `web/src/**/*.{ts,tsx}`).

### Category 1: SQL / SQLite Injection and Tampering
- Are all DB reads/writes parameterized? Any raw string-interpolated SQL?
- Are save/state blobs (JSON columns) validated before trust?
- Are numeric values (treasury, faction power, trust) bounds-checked before write?
- Any `eval()` / `exec()` / `pickle.loads()` on DB or user content?
- Is the SQLite file path ever derived from user input? (path traversal)

Grep: `execute(`, `executemany(`, `f".*SELECT|INSERT|UPDATE|DELETE"`, `eval(`, `exec(`, `pickle`, `json.loads` in `ming_sim/*.py`. Check each SQL call site uses `?` placeholders, not f-strings.

### Category 2: LLM Prompt Injection and Data Exposure
- Are NPC/character names or user-authored strings concatenated into LLM prompts without delimiting/quoting? (indirect prompt injection)
- Can a player-authored value (decree text, audience message, petition) override system instructions?
- Are secrets (API keys, system prompts considered proprietary) logged or echoed back to the client?
- Is there a token/cost ceiling to prevent a malicious input from burning budget?
- Are LLM tool calls (agno) sandboxed — can the agent write to arbitrary DB rows or call arbitrary Python?

Grep: `audit_client`, `agent.run`, `Agent(`, `system_prompt`, `instructions=`, `f".*{.*}.*prompt"` in `ming_sim/*.py`. Review how `dialogue_audit.py`, `dialogue_semantics.py`, and any `*_probe.py` build prompts.

### Category 3: Input Validation (FastAPI + client)
- Do all write endpoints declare a Pydantic model (not bare `dict` / `Request.json()`)?
- Are string fields length-bounded? Numeric fields range-bounded?
- Are foreign-key-style IDs (npc_id, character_name, turn) validated against existence before use?
- Is there any `eval`/`os.system`/`subprocess(shell=True)` reached by request data?

Grep: `@app.post`, `@router.post`, `: dict`, `Request.json`, `request.json()`, `os.system`, `subprocess` in `ming_sim/*.py`. Cross-check routes against `web_route_contracts.py`.

### Category 4: Authentication / Authorization
- Are admin/management routes (port >game port, whitelist tables) gated by role + audit log, per `docs/engineering-architecture.md` §运行时依赖从轻 / 管理平台走受限端口?
- Are game endpoints isolated from admin endpoints?
- Is there any unauthenticated write path to game state?
- Are session/turn-mutation endpoints idempotent where they must be (CAS gates)?

Grep: `Depends(`, `require_`, `admin`, `whitelist`, `role` in `ming_sim/*.py` and `web_app.py`.

### Category 5: Secret and Data Exposure
- Are any API keys, tokens, DB paths, or system prompts hardcoded in `ming_sim/`, `scripts/`, or committed `.env`?
- Do error responses leak stack traces / SQL / internal paths to the client?
- Does the backend log sensitive player data (secrets, grievance, trust) to disk unredacted?
- Are debug endpoints (`/docs`, `/redoc`, probe scripts) enabled in a deploy config?

Grep: `api_key`, `secret`, `password`, `token`, `OPENAI`, `ANTHROPIC`, `DEBUG`, `print(`, `raise Exception(` in `ming_sim/*.py` and `scripts/*.py`. Check `.gitignore` covers `.env*`, `*.db`, `*.sqlite*`.

### Category 6: Dependency and Supply Chain
- List third-party packages from `requirements.txt` / `pyproject.toml`.
- Pin major versions? Any `*` / unbounded?
- Any package with a known CVE in the pinned range?

Glob: `requirements*.txt`, `pyproject.toml`, `package.json`, `web/package.json`. Run `pip list --outdated` is NOT required — note versions only.

---

## Phase 3: Classify Findings

For each finding assign:

**Severity:**
| Level | Definition |
|-------|-----------|
| **CRITICAL** | SQL injection, RCE, auth bypass, or LLM prompt injection that can corrupt game state |
| **HIGH** | State tampering that bypasses progression, credential/key exposure, missing auth on admin route |
| **MEDIUM** | Input validation gap with limited impact, info disclosure, missing idempotency on non-critical path |
| **LOW** | Defense-in-depth improvement; no direct exploit |

**Status:** Open / Accepted Risk / Out of Scope

---

## Phase 4: Generate Report

```markdown
# Security Audit Report — 大明王朝1628

**Date**: [date]
**Scope**: [full | db | llm | input | api | quick]
**Stack**: Python [ver] / FastAPI / SQLite / agno / React-TS
**Files scanned**: [N] .py, [N] .ts/.tsx, [N] config

## Executive Summary
| Severity | Count | Must Fix Before Release |
|----------|-------|------------------------|
| CRITICAL | [N] | Yes — all |
| HIGH | [N] | Yes — all |
| MEDIUM | [N] | Recommended |
| LOW | [N] | Optional |

**Release recommendation**: [CLEAR / FIX CRITICALS FIRST / DO NOT SHIP]

## CRITICAL Findings
### SEC-001: [Title]
**Category**: [db / llm / input / api / secret / dep]
**File**: `[path]` line [N]
**Description**: ...
**Attack scenario**: ...
**Remediation**: ...
**Effort**: [Low / Medium / High]

## HIGH / MEDIUM / LOW Findings
[same format]

## Dependency Inventory
| Package | Version | Source | Known CVEs |
|---------|---------|--------|------------|

## Remediation Priority Order
1. [SEC-NNN] — [desc] — [effort]
```

---

## Phase 5: Write Report

Present the executive summary + CRITICAL/HIGH findings in conversation.
Ask: "May I write the full security audit report to `docs/security/security-audit-[date].md`?"
Write only after approval.

---

## Collaborative Protocol
- **Never assume a pattern is safe** — flag it and let the user decide.
- **Accepted risk is valid** — some LOW findings are acceptable trade-offs; document the decision.
- **This is not a pentest** — covers common patterns; a human review is recommended before any public deploy.
- **LLM-specific risks are first-class here** — this project's LLM audit pipeline is a primary attack surface, not an afterthought.
