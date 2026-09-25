"""
LangGraph Orchestrator — the main state machine that drives investigations.

Flow:
    Trigger → Investigate → Assess ──sufficient?──┐
                 ▲            │ no (max 3 loops)   │
                 │            ▼                    │
                 └── GatherMoreEvidence            │
                              ↓ (re-assess)        │
                                                   ▼
                                     TakeAction → Explain → WriteToGraph

The orchestrator can run with or without LangGraph installed.
When LangGraph is not available, it falls back to a simple sequential
loop that achieves the same result.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from agent.state import CaseState
from agent.tools.tigergraph_tools import DataLayer
from agent.nodes.trigger import run_trigger
from agent.nodes.investigate import run_investigate
from agent.nodes.assess import run_assess
from agent.nodes.gather_more import run_gather_more
from agent.nodes.take_action import run_take_action
from agent.nodes.explain import run_explain
from agent.nodes.write_to_graph import run_write_to_graph
from agent import config

logger = logging.getLogger(__name__)


class FraudInvestigationAgent:
    """
    The main fraud investigation agent.

    Processes a case from the case pack through the full investigation
    pipeline and produces a validated answer file.
    """

    def __init__(self, data_layer: DataLayer | None = None):
        self.data = data_layer or DataLayer()

    def investigate_case(self, case_row: dict[str, Any]) -> CaseState:
        """
        Run the full investigation pipeline on a single case.

        Args:
            case_row: A row from case_pack.csv as a dict.

        Returns:
            Completed CaseState with all fields populated.
        """
        case_id = case_row.get("case_id", "UNKNOWN")
        logger.info(f"{'='*60}")
        logger.info(f"Starting investigation: {case_id}")
        logger.info(f"{'='*60}")

        # Initialize state
        state = CaseState()
        state._start_time = time.time()

        # Reset tool call counter for this case
        self.data.tool_call_count = 0

        try:
            # ── 1. TRIGGER ─────────────────────────────────────────────────
            state = run_trigger(state, case_row)

            # ── 2. INVESTIGATE ─────────────────────────────────────────────
            state = run_investigate(state, self.data)

            # ── 3. INITIAL ASSESS ──────────────────────────────────────────
            state = run_assess(state)

            # ── 4. INITIAL ACTION (pre-evidence) ──────────────────────────
            state = run_take_action(state)

            # ── 5. EVIDENCE LOOP ───────────────────────────────────────────
            while (
                not state.sufficient_evidence
                and state.evidence_loop_count < config.MAX_EVIDENCE_LOOPS
            ):
                # Gather more evidence
                state = run_gather_more(state)

                # Re-assess with new evidence
                state = run_assess(state)

                # Update actions (post-evidence)
                state = run_take_action(state)

            # ── 6. EXPLAIN ─────────────────────────────────────────────────
            state = run_explain(state)

            # ── 7. WRITE TO GRAPH ──────────────────────────────────────────
            state = run_write_to_graph(state)

            # ── Finalize ───────────────────────────────────────────────────
            state.tool_calls = self.data.tool_call_count
            state.latency_s = state.elapsed()

            if not state.stop_reason:
                state.stop_reason = (
                    f"Investigation complete. Verdict: {state.verdict.value} "
                    f"with fraud probability {state.fraud_probability:.2f}."
                )

            logger.info(
                f"[{case_id}] Investigation complete: "
                f"{state.verdict.value} (p={state.fraud_probability:.2f}), "
                f"pattern={state.pattern.value}, "
                f"exposure=${state.exposure_usd:.2f}, "
                f"actions={len(state.next_best_actions.final)}, "
                f"SAR={'yes' if state.sar.file else 'no'}, "
                f"tool_calls={state.tool_calls}, "
                f"latency={state.latency_s:.1f}s"
            )

        except Exception as e:
            logger.error(f"[{case_id}] Investigation failed: {e}", exc_info=True)
            state.stop_reason = f"Investigation failed with error: {str(e)}"
            state.latency_s = state.elapsed()

        return state

    def save_answer(self, state: CaseState, output_dir: str | Path | None = None) -> Path:
        """
        Save the case state as an answer JSON file.

        Args:
            state: Completed CaseState
            output_dir: Directory to save to (default: benchmark/cases/)

        Returns:
            Path to the saved file.
        """
        output_dir = Path(output_dir or config.CASES_OUTPUT_DIR)
        output_dir.mkdir(parents=True, exist_ok=True)

        answer = state.to_answer_dict()
        filepath = output_dir / f"{state.case_id}.json"

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(answer, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"[{state.case_id}] Answer saved to {filepath}")
        return filepath


def run_single_case(case_id: str) -> Path:
    """
    Convenience function to investigate a single case by ID.
    Loads the case pack, finds the case, and runs the full pipeline.
    """
    data = DataLayer()
    agent = FraudInvestigationAgent(data)

    # Find the case in the pack
    case_pack = data.case_pack
    case_row = case_pack[case_pack["case_id"] == case_id]

    if case_row.empty:
        raise ValueError(f"Case {case_id} not found in case pack")

    row_dict = case_row.iloc[0].to_dict()
    state = agent.investigate_case(row_dict)
    return agent.save_answer(state)


def run_all_cases() -> list[Path]:
    """
    Run all 20 cases from the case pack.
    Returns list of paths to generated answer files.
    """
    data = DataLayer()
    agent = FraudInvestigationAgent(data)
    case_pack = data.case_pack

    results = []
    for _, row in case_pack.iterrows():
        row_dict = row.to_dict()
        state = agent.investigate_case(row_dict)
        path = agent.save_answer(state)
        results.append(path)

    return results


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) > 1:
        case_id = sys.argv[1]
        print(f"Running case {case_id}...")
        path = run_single_case(case_id)
        print(f"Answer saved to {path}")
    else:
        print("Running all 20 cases...")
        paths = run_all_cases()
        print(f"\nCompleted {len(paths)} cases:")
        for p in paths:
            print(f"  {p}")
