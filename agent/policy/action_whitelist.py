"""
Action whitelist — Policy §1.

The agent may ONLY select actions from this set.
Any action not in the whitelist is rejected and triggers regeneration.
"""

from agent.state import ActionType, ApprovalRoute


# All valid actions the agent can recommend
ACTION_WHITELIST: set[str] = {a.value for a in ActionType}


def validate_action(action_name: str) -> bool:
    """Return True if the action is in the whitelist."""
    return action_name in ACTION_WHITELIST


def validate_actions(actions: list[dict]) -> tuple[bool, list[str]]:
    """
    Validate a list of action recommendations.
    Returns (all_valid, list_of_invalid_action_names).
    """
    invalid = []
    for a in actions:
        name = a.get("action", "")
        if not validate_action(name):
            invalid.append(name)
    return len(invalid) == 0, invalid
