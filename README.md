# HHGoa — TigerGraph Agentic Fraud Investigation Agent

An agentic fraud investigation system built for the **Hacker House Goa 2026** hackathon by TigerGraph. The agent takes 20 case-pack alerts from the IEEE-CIS fraud dataset, investigates them using a TigerGraph knowledge graph + GraphRAG, and produces scored answer files.

## Architecture

```
                    ┌─────────────────────────────┐
                    │   Benchmark Runner            │
                    │   (run_all.py)                │
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
                    │   Agent Orchestrator          │
                    │   (LangGraph State Machine)   │
                    │                               │
                    │  Trigger → Investigate →       │
                    │  Assess → (Evidence Loop) →    │
                    │  Act → Explain → WriteToGraph  │
                    └──┬───────────────┬───────────┘
                       │               │
          ┌────────────▼───┐   ┌───────▼─────────┐
          │  Data Layer     │   │  Policy Engine   │
          │  (TigerGraph /  │   │  (R1-R10 rules,  │
          │   Pandas local) │   │   whitelist,      │
          │                 │   │   approval routes) │
          └────────┬────────┘   └─────────────────┘
                   │
          ┌────────▼────────────────────────────┐
          │   IEEE-CIS Dataset (590k txns)       │
          │   + 5,565 closed cases               │
          │   + 20 case pack alerts              │
          └──────────────────────────────────────┘
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Place the dataset

Ensure the IEEE-CIS dataset files are in the `HHGOA_IEEE/` directory alongside this project:
- `transactions.csv` (590k transactions, ~708 MB)
- `identity.csv` (144k identity records)
- `closed_cases_history.csv` (5,565 closed cases)
- `case_pack.csv` (20 exam cases)

### 4. Run a single case

```bash
python -m agent.orchestrator HHG-001
```

### 5. Run all 20 cases

```bash
python -m benchmark.run_all
```

### 6. Validate answer files

```bash
python -m benchmark.answer_schema benchmark/cases/
```

### 7. Self-score

```bash
python -m benchmark.scorer benchmark/cases/
```

## With TigerGraph (optional for enhanced performance)

```bash
# Start TigerGraph CE
docker compose up -d tigergraph

# Wait for TG to be ready, then load schema
docker exec -it hhgoa-tigergraph gsql -g FraudGraph < graph/schema.gsql
```

## Project Structure

```
hhgoa-fraud-agent/
├── agent/                   # Core investigation agent
│   ├── orchestrator.py      # LangGraph state machine
│   ├── state.py             # Case state schema
│   ├── config.py            # Thresholds and settings
│   ├── nodes/               # State machine nodes
│   │   ├── trigger.py       # Parse case triggers
│   │   ├── investigate.py   # Run graph queries
│   │   ├── assess.py        # LLM assessment + fallback
│   │   ├── gather_more.py   # Evidence request loop
│   │   ├── take_action.py   # Policy-gated actions
│   │   ├── explain.py       # Summary + SAR generation
│   │   └── write_to_graph.py
│   ├── tools/               # Graph query wrappers
│   ├── policy/              # R1-R10 rules engine
│   └── prompts/             # LLM prompt templates
├── graph/                   # TigerGraph schema + queries
│   └── schema.gsql
├── benchmark/               # Evaluation tools
│   ├── run_all.py           # Run all 20 cases
│   ├── answer_schema.py     # JSON schema validator
│   ├── scorer.py            # Self-scoring harness
│   └── cases/               # Generated answer files
├── docs/                    # Documentation
├── docker-compose.yml       # TigerGraph CE
└── requirements.txt
```

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Agent framework | LangGraph (with sequential fallback) | Investigation flow is explicitly a state machine with conditional edges |
| Graph database | TigerGraph CE via Docker | Full GSQL control, snapshot-able, free |
| Data layer | Pandas with TigerGraph upgrade path | Works without TG for development; identical query interface |
| LLM | Claude (Anthropic) with deterministic fallback | Agent runs without API key using heuristic assessment |
| Policy enforcement | Deterministic code, not prompts | R1-R10 rules, action whitelist, and approval routing are hard-coded |
| Answer format | Exact README schema | `answer_schema.py` validates every field before submission |

## Scoring

The self-scoring harness (`benchmark/scorer.py`) evaluates against the judging rubric:

| Category | Weight | What's scored |
|---|---|---|
| Investigation accuracy | 25% | Evidence coverage, pattern detection, citation rate |
| Next-best-action | 25% | Policy compliance, approval routing, pre/post actions |
| Explainability | 10% | Evidence citations, summary quality, SAR narrative |
| Agentic design | 15% | Evidence loop, decisions log, stop reasoning |
| Completeness | 15% | Schema validity, graph write-back, field coverage |

## Attribution

IEEE-CIS Fraud Detection dataset, Vesta Corporation, via the IEEE Computational Intelligence Society. Extended by TigerGraph for Hacker House Goa 2026.
