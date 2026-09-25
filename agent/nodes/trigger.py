"""
Trigger node — parse the case pack entry and initialize the case state.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.state import CaseState, TriggerInfo, TriggerType

logger = logging.getLogger(__name__)


def run_trigger(state: CaseState, case_row: dict[str, Any]) -> CaseState:
    """
    Parse a row from case_pack.csv and initialize the CaseState.
    This is the entry point for every investigation.
    """
    # Parse trigger type
    trigger_type_str = case_row.get("trigger_type", "risk_score")
    try:
        trigger_type = TriggerType(trigger_type_str)
    except ValueError:
        trigger_type = TriggerType.RISK_SCORE

    # Parse risk score (only present for risk_score triggers)
    risk_score = case_row.get("risk_score")
    if risk_score and str(risk_score).strip() not in ("", "—", "nan"):
        try:
            risk_score = float(risk_score)
        except (ValueError, TypeError):
            risk_score = None
    else:
        risk_score = None

    state.case_id = str(case_row["case_id"])
    state.trigger = TriggerInfo(
        trigger_type=trigger_type,
        trigger_text=str(case_row.get("trigger_text", "")),
        flagged_txn_id=str(case_row["flagged_txn_id"]),
        card_id=str(case_row["card_id"]),
        customer_id=str(case_row["customer_id"]),
        risk_score=risk_score,
        opened_at=str(case_row.get("opened_at", "")),
    )

    # Set initial fraud probability from risk score if available
    if risk_score is not None:
        # Risk score is a starting point, not a verdict
        # Scale it slightly toward 0.5 to avoid over-reliance
        state.fraud_probability = 0.5 + (risk_score - 0.5) * 0.4
    elif trigger_type == TriggerType.CUSTOMER_REPORT:
        # Customer reports start at moderate probability
        state.fraud_probability = 0.50
    elif trigger_type == TriggerType.ANALYST_REQUEST:
        # Analyst requests suggest something worth investigating
        state.fraud_probability = 0.50
    else:
        state.fraud_probability = 0.50

    state.log_decision(
        "TRIGGER",
        f"Case {state.case_id} opened. Trigger: {trigger_type.value}. "
        f"Flagged txn: {state.trigger.flagged_txn_id}. "
        f"Initial fraud probability: {state.fraud_probability:.2f}."
    )

    logger.info(f"[{state.case_id}] Trigger processed: {trigger_type.value}")
    return state
