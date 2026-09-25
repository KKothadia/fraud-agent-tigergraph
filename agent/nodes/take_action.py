"""
Take Action node — apply policy rules to recommend actions.

Uses the deterministic policy engine (rules R1-R10, approval routing)
to produce initial and final action recommendations.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.state import CaseState, ActionRecommendation, NextBestActions
from agent.policy.rules import recommend_actions
from agent.policy.action_whitelist import validate_actions

logger = logging.getLogger(__name__)


def run_take_action(state: CaseState) -> CaseState:
    """
    Apply policy rules to determine the next best actions.

    Produces:
    - initial actions (before evidence requests)
    - final actions (after evidence responses)
    - what_changed description
    """
    logger.info(f"[{state.case_id}] Determining actions")

    # Get actions from the policy engine
    actions = recommend_actions(state)

    # Validate all actions against whitelist
    action_dicts = [{"action": a.action} for a in actions]
    valid, invalid = validate_actions(action_dicts)
    if not valid:
        logger.error(f"[{state.case_id}] Invalid actions rejected: {invalid}")
        actions = [a for a in actions if a.action not in invalid]

    # Determine if we have initial vs final distinction
    if state.evidence_requests:
        # We have evidence requests — need both initial and final
        if not state.next_best_actions.initial:
            # First time — these are the initial actions (pre-evidence)
            state.next_best_actions.initial = actions
            state.next_best_actions.final = actions
        else:
            # We already have initial — update final
            state.next_best_actions.final = actions

            # Describe what changed
            initial_set = {a.action for a in state.next_best_actions.initial}
            final_set = {a.action for a in state.next_best_actions.final}

            added = final_set - initial_set
            removed = initial_set - final_set

            changes = []
            if added:
                changes.append(f"Added: {', '.join(added)}")
            if removed:
                changes.append(f"Removed: {', '.join(removed)}")

            # Check for probability change
            changes.append(
                f"Fraud probability updated to {state.fraud_probability:.2f}"
            )

            if changes:
                state.next_best_actions.what_changed = ". ".join(changes)
            else:
                state.next_best_actions.what_changed = "nothing"
    else:
        # No evidence requests — initial equals final
        state.next_best_actions.initial = actions
        state.next_best_actions.final = actions
        state.next_best_actions.what_changed = "nothing"

    # Log the actions
    action_summary = ", ".join(
        f"{a.action}({a.route})" for a in state.next_best_actions.final
    )

    state.log_decision(
        "TAKE_ACTION",
        f"Recommended actions: {action_summary}. "
        f"Policy rules applied: {', '.join(set(a.reason.split(':')[0] for a in state.next_best_actions.final))}."
    )

    logger.info(f"[{state.case_id}] Actions: {action_summary}")

    return state
