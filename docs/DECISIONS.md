# Decision Log

Append-only log of architectural and design decisions.
Each entry is marked as **Decision** (team-made choice) or **Fact** (dataset/brief constraint).

---

## D001: Agent Framework → LangGraph with Sequential Fallback

**Type:** Decision
**Date:** 2026-09-20
**Context:** The investigation flow is explicitly a state machine (Trigger → Investigate → Assess → maybe-loop → Act → Explain → Write). LangGraph maps directly to this with conditional edges.
**Decision:** Use LangGraph as the primary orchestrator. Implement a sequential fallback loop that achieves the same state transitions without the framework dependency.
**Rationale:** LangGraph's graph-of-nodes model matches the brief's 8-step flow naturally. The fallback ensures the agent runs even if LangGraph has version issues.

---

## D002: TigerGraph Hosting → Community Edition on Docker

**Type:** Decision
**Date:** 2026-09-20
**Context:** Savanna (cloud) offers managed hosting but has query-time limits and requires internet. CE on Docker gives full GSQL control and works offline for judging.
**Decision:** Default to CE via Docker. Document Savanna as alternative with auto-stop/auto-start for cost optimization.
**Rationale:** Judges need `docker compose up` to work. CE is free, snapshot-able, and has no query limits.

---

## D003: Data Layer → Pandas with TigerGraph Upgrade Path

**Type:** Decision
**Date:** 2026-09-20
**Context:** Development and testing need to work without a running TigerGraph instance. The same queries must produce identical results from both pandas and TG.
**Decision:** Implement all graph queries as pandas DataFrame operations first. When TigerGraph is available, transparently forward queries there.
**Rationale:** Fastest path to end-to-end working agent. The pandas layer also serves as the GSQL test oracle.

---

## D004: LLM Provider → Anthropic Claude with Deterministic Fallback

**Type:** Decision
**Date:** 2026-09-20
**Context:** LLM is used for assessment synthesis, explanation generation, and SAR narrative writing. Not all team members have API keys during development.
**Decision:** Default to Claude (claude-sonnet-4-20250514). When no API key is configured, fall back to deterministic heuristics that use pattern match scores, evidence counts, and policy rules.
**Rationale:** The agent must produce valid answer files with or without an LLM. The fallback also demonstrates that graph queries, not LLM reasoning, are the investigation moat.

---

## D005: Policy Enforcement → Deterministic Code, Not Prompts

**Type:** Fact + Decision
**Context:** The README specifies exact action names, approval routes, and 10 numbered policy rules. The brief says "operate within predefined policies, enforced as a whitelist in code, not a prompt instruction."
**Decision:** All 10 rules (R1-R10) are implemented as Python functions. The action whitelist is an enum. Approval routing is a lookup table with exposure-based logic.
**Rationale:** Prompts can hallucinate actions. Code cannot. This also makes policy compliance unit-testable.

---

## D006: Answer Format → Exact README Schema, Validated Automatically

**Type:** Fact
**Context:** The README specifies the exact JSON schema for answer files, including field names, types, and enum values. "Missing fields score zero."
**Decision:** Implement `answer_schema.py` with validators for every field. CI rejects any answer file that fails validation.
**Rationale:** Format compliance is trivially verifiable and worth a significant portion of the score.

---

## D007: Confidence Threshold → Policy §6, Logged in Every Explanation

**Type:** Fact + Decision
**Context:** Policy §6 specifies three stop conditions. The brief calls out "determine when to stop" as a separately judged bullet.
**Decision:** Implement `should_stop()` with explicit thresholds (≥0.85 or ≤0.15 with ≥2 evidence, verification settles, further steps useless). Log the threshold value in every case explanation.
**Rationale:** Judges look for visible, logged stop reasoning. The threshold appears in the decisions_log and the explanation.
