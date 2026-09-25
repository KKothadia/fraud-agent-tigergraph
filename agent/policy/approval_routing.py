"""
Approval routing — Policy §2.

Deterministic code, not prompt-driven. Each action maps to an approval route
based on action type and exposure amount.
"""

from agent.state import ActionType, ApprovalRoute
from agent import config


# ── Static routing table ───────────────────────────────────────────────────────
# Actions that are always auto-approved
AUTO_ACTIONS: set[str] = {
    ActionType.ALLOW_TRANSACTION.value,
    ActionType.MONITOR_CARD.value,
    ActionType.MONITOR_CONNECTED_CARDS.value,
    ActionType.WARN_CUSTOMER.value,
    ActionType.VERIFY_WITH_CUSTOMER.value,
    ActionType.STEP_UP_AUTH.value,
    ActionType.GENERATE_REPORT.value,
    ActionType.CREATE_CASE.value,
    ActionType.ESCALATE_TO_ANALYST.value,
    ActionType.CLOSE_NO_FRAUD.value,
}

# Actions that always require L2
L2_ALWAYS_ACTIONS: set[str] = {
    ActionType.FILE_REPORT.value,
    ActionType.BLOCK_ALL_CARDS.value,
}


def get_approval_route(action_name: str, exposure_usd: float = 0.0) -> str:
    """
    Determine the approval route for a given action.

    Policy §2:
    - auto: most actions
    - L1: DECLINE_TRANSACTION; BLOCK_CARD when exposure ≤ $2,500
    - L2: BLOCK_CARD when exposure > $2,500; BLOCK_ALL_CARDS always; FILE_REPORT always
    """
    if action_name in AUTO_ACTIONS:
        return ApprovalRoute.AUTO.value

    if action_name in L2_ALWAYS_ACTIONS:
        return ApprovalRoute.L2.value

    if action_name == ActionType.DECLINE_TRANSACTION.value:
        return ApprovalRoute.L1.value

    if action_name == ActionType.BLOCK_CARD.value:
        if exposure_usd <= config.BLOCK_CARD_L1_THRESHOLD:
            return ApprovalRoute.L1.value
        else:
            return ApprovalRoute.L2.value

    # Fallback — should not be reached if whitelist is enforced
    return ApprovalRoute.L1.value
