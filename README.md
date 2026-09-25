# 🐅 HHGoa — TigerGraph Agentic Fraud Investigation Agent

![TigerGraph](https://img.shields.io/badge/TigerGraph-Hackathon-orange) ![Python](https://img.shields.io/badge/Python-3.12-blue) ![LangGraph](https://img.shields.io/badge/LangGraph-Agentic-green)

An autonomous agentic fraud investigation system built for the **Hacker House Goa 2026** hackathon by TigerGraph. 

This agent processes complex case-pack alerts from the IEEE-CIS fraud dataset, autonomously investigates them using a TigerGraph knowledge graph and GraphRAG, strictly enforces organizational policies, and produces highly explainable investigation reports.

## 🧠 Architecture

The system uses a state-machine architecture powered by **LangGraph** to ensure deterministic, auditable, and reliable investigations.

```text
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

### 🧩 Core Components

1. **Agent Orchestrator**: A cyclic directed graph that steps through the investigation lifecycle.
2. **Policy Engine**: A deterministic rule engine ensuring all actions comply with organizational rules (R1-R10), approval routing constraints, and whitelists.
3. **Evidence Loop**: An autonomous sub-routine where the agent evaluates if it has enough data to make a decision, requesting deeper graph traversals if necessary.
4. **Data Layer**: A scalable interface that interacts with TigerGraph for deep multi-hop relationship queries (e.g., shared devices, IP velocity, identity rings).

## 🚀 Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your API keys (e.g., OPENROUTER_API_KEY)
```

### 3. Place the dataset

Ensure the IEEE-CIS dataset files are in the `HHGOA_IEEE/` directory alongside this project:
- `transactions.csv` (590k transactions, ~708 MB)
- `identity.csv` (144k identity records)
- `closed_cases_history.csv` (5,565 closed cases)
- `case_pack.csv` (20 exam cases)

### 4. Run Investigations

**Run a single case:**
```bash
python -m agent.orchestrator HHG-001
```

**Run all 20 benchmark cases:**
```bash
python -m benchmark.run_all
```

**Validate output schemas:**
```bash
python -m benchmark.answer_schema benchmark/cases/
```

## 🐅 TigerGraph Integration (Optional)

For enhanced performance and true multi-hop pattern detection, run the agent with a live TigerGraph instance:

```bash
# Start TigerGraph CE
docker compose up -d tigergraph

# Wait for TG to be ready, then load schema
docker exec -it hhgoa-tigergraph gsql -g FraudGraph < graph/schema.gsql
```

## 📂 Project Structure

```text
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
│   │   └── write_to_graph.py# Persistence 
│   ├── tools/               # Graph query wrappers
│   ├── policy/              # R1-R10 rules engine
│   └── prompts/             # LLM prompt templates
├── graph/                   # TigerGraph schema + queries
├── benchmark/               # Evaluation tools (runner, schema tests)
├── docs/                    # Deep-dive documentation
├── docker-compose.yml       # TigerGraph CE environment
└── requirements.txt         # Python dependencies
```

## ⚖️ Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Agent Framework** | LangGraph | Ensures the investigation flow is explicitly controlled via a state machine with conditional edges, avoiding infinite agent loops and ensuring auditable decision points. |
| **Graph Database** | TigerGraph CE | Provides full GSQL control, enabling deep multi-hop queries that relational databases or Pandas cannot efficiently perform (e.g., finding fraudulent identity rings). |
| **Data Layer Abstraction** | Dual Interface | Can gracefully fallback to Pandas for development without TigerGraph, while presenting an identical query interface to the agent. |
| **LLM Assessment** | Deterministic Fallback | Agent can run without an API key using heuristic assessment for high-throughput baseline testing. |
| **Policy Enforcement** | Hardcoded Logic | R1-R10 rules, action whitelists, and approval routing are strictly enforced by deterministic code, not prompts, to guarantee 100% policy compliance. |

## 📜 Attribution

Dataset based on the IEEE-CIS Fraud Detection dataset, Vesta Corporation, via the IEEE Computational Intelligence Society. Extended for Hacker House Goa 2026.
