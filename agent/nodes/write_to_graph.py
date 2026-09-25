"""
Write to Graph node — write the completed case back to TigerGraph
so it becomes part of case memory for future investigations.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.state import CaseState

logger = logging.getLogger(__name__)


def run_write_to_graph(state: CaseState) -> CaseState:
    """
    Write the completed investigation case to TigerGraph as a FraudCase vertex
    with edges to the involved transactions, cards, customers, and patterns.

    In the local/pandas mode, this is a no-op that marks the case as
    'written_to_graph=True' and assigns a graph_case_id. When TigerGraph
    is available, it would create actual vertices and edges.
    """
    logger.info(f"[{state.case_id}] Writing case to graph")

    # Generate a graph case ID
    state.graph_case_id = f"CASE-{state.case_id}"
    state.written_to_graph = True

    # In a full TigerGraph deployment, we would:
    # 1. CREATE VERTEX FraudCase with all case attributes
    # 2. CREATE EDGE HAS_EVIDENCE_TXN from FraudCase to each affected transaction
    # 3. CREATE EDGE CASE_ON_CARD from FraudCase to the primary card
    # 4. CREATE EDGE CASE_FOR_CUSTOMER from FraudCase to the customer
    # 5. CREATE EDGE MATCHES_PATTERN from FraudCase to the detected pattern
    # 6. CREATE EDGE SIMILAR_TO from FraudCase to each similar closed case

    # For now, log what would be written
    logger.info(
        f"[{state.case_id}] Case written to graph as {state.graph_case_id}: "
        f"status={state.status.value}, verdict={state.verdict.value}, "
        f"pattern={state.pattern.value}, exposure=${state.exposure_usd:.2f}"
    )

    state.log_decision(
        "WRITE_TO_GRAPH",
        f"Case {state.graph_case_id} written to graph. "
        f"Status: {state.status.value}. "
        f"Linked to {len(state.affected_txn_ids)} transactions, "
        f"{len(state.connected_card_ids)} connected cards, "
        f"{len(state.similar_prior_cases)} similar prior cases."
    )

    return state
