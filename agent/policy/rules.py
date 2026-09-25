"""
Fraud policy rules R1–R10, encoded as deterministic functions.

Each rule takes structured inputs (assessment, evidence, exposure, etc.)
and returns a list of ActionRecommendation objects. The orchestrator
composes them; rules never call each other.

All rule numbers, action names, and approval routes come from the README
Fraud Policy section.
"""

from __future__ import annotations

from agent.state import (
    ActionRecommendation,
    ActionType,
    CaseState,
    Verdict,
    FraudPattern,
)
from agent.policy.approval_routing import get_approval_route
from agent import config


def _action(action: str, exposure: float, reason: str) -> ActionRecommendation:
    """Helper to build an ActionRecommendation with auto-computed route."""
    return ActionRecommendation(
        action=action,
        route=get_approval_route(action, exposure),
        reason=reason,
    )


# ── R1: Verify before blocking on weak signal ─────────────────────────────────

def apply_R1(
    fraud_probability: float,
    independent_evidence_count: int,
) -> list[ActionRecommendation]:
    """
    R1: If the case rests on a single signal and fraud probability < 0.70,
    recommend VERIFY_WITH_CUSTOMER or STEP_UP_AUTH before any block.
    """
    if independent_evidence_count <= 1 and fraud_probability < 0.70:
        return [
            _action(ActionType.VERIFY_WITH_CUSTOMER.value, 0, "R1: single signal, probability below 0.70 — verify before blocking"),
        ]
    return []


# ── R2: Customer denies the transaction ────────────────────────────────────────

def apply_R2(
    customer_denied: bool,
    exposure_usd: float,
    has_shared_device: bool,
    has_connected_fraud: bool,
) -> list[ActionRecommendation]:
    """
    R2: Customer denies → BLOCK_CARD + CREATE_CASE.
    Add FILE_REPORT if exposure > $1,000 or shared device/connected fraud.
    """
    if not customer_denied:
        return []

    actions = [
        _action(ActionType.BLOCK_CARD.value, exposure_usd, "R2: customer denied the transaction"),
        _action(ActionType.CREATE_CASE.value, 0, "R2: customer denied the transaction"),
    ]

    if exposure_usd > config.SAR_EXPOSURE_THRESHOLD or has_shared_device or has_connected_fraud:
        reason_parts = []
        if exposure_usd > config.SAR_EXPOSURE_THRESHOLD:
            reason_parts.append(f"exposure ${exposure_usd:.2f} exceeds ${config.SAR_EXPOSURE_THRESHOLD:.0f}")
        if has_shared_device:
            reason_parts.append("shared device profile detected")
        if has_connected_fraud:
            reason_parts.append("connected to another card's fraud")
        actions.append(_action(
            ActionType.FILE_REPORT.value,
            exposure_usd,
            f"R2: {'; '.join(reason_parts)}",
        ))

    return actions


# ── R3: Customer confirms the transaction ──────────────────────────────────────

def apply_R3(customer_confirmed: bool) -> list[ActionRecommendation]:
    """R3: Customer confirms → CLOSE_NO_FRAUD."""
    if customer_confirmed:
        return [
            _action(ActionType.CLOSE_NO_FRAUD.value, 0, "R3: customer confirmed the transaction"),
        ]
    return []


# ── R4: No reply within 24 hours ──────────────────────────────────────────────

def apply_R4(
    no_reply: bool,
    exposure_usd: float,
) -> list[ActionRecommendation]:
    """
    R4: No reply → MONITOR_CARD + DECLINE_TRANSACTION.
    Escalate if exposure > $500.
    """
    if not no_reply:
        return []

    actions = [
        _action(ActionType.MONITOR_CARD.value, 0, "R4: no customer reply within 24 hours"),
        _action(ActionType.DECLINE_TRANSACTION.value, exposure_usd, "R4: no reply, decline pending authorizations"),
    ]

    if exposure_usd > config.ESCALATION_EXPOSURE_THRESHOLD:
        actions.append(_action(
            ActionType.ESCALATE_TO_ANALYST.value,
            0,
            f"R4: no reply and exposure ${exposure_usd:.2f} exceeds ${config.ESCALATION_EXPOSURE_THRESHOLD:.0f}",
        ))

    return actions


# ── R5: Card testing ──────────────────────────────────────────────────────────

def apply_R5(
    is_card_testing: bool,
    large_purchase_cleared: bool,
    exposure_usd: float,
) -> list[ActionRecommendation]:
    """
    R5: Card testing pattern → DECLINE_TRANSACTION + STEP_UP_AUTH.
    If a purchase > $100 already cleared → BLOCK_CARD.
    """
    if not is_card_testing:
        return []

    actions = [
        _action(ActionType.DECLINE_TRANSACTION.value, exposure_usd, "R5: card testing sequence detected"),
        _action(ActionType.STEP_UP_AUTH.value, 0, "R5: require authentication after testing pattern"),
    ]

    if large_purchase_cleared:
        actions.append(_action(
            ActionType.BLOCK_CARD.value,
            exposure_usd,
            f"R5: purchase over ${config.CARD_TESTING_CLEARED_THRESHOLD:.0f} already cleared",
        ))

    return actions


# ── R6: Shared origin ─────────────────────────────────────────────────────────

def apply_R6(
    shared_element: str | None,
    shared_element_type: str = "",
    exposure_usd: float = 0.0,
) -> list[ActionRecommendation]:
    """
    R6: Multiple cards from same device/region/email → CREATE_CASE +
    FILE_REPORT + MONITOR_CONNECTED_CARDS.
    """
    if not shared_element:
        return []

    return [
        _action(ActionType.CREATE_CASE.value, 0,
                f"R6: shared {shared_element_type} '{shared_element}' across multiple cards"),
        _action(ActionType.FILE_REPORT.value, exposure_usd,
                f"R6: shared {shared_element_type} links fraud across cards"),
        _action(ActionType.MONITOR_CONNECTED_CARDS.value, 0,
                f"R6: monitor all cards sharing {shared_element_type} '{shared_element}'"),
    ]


# ── R7: Disputed but legitimate (recurring pattern) ───────────────────────────

def apply_R7(
    is_recurring_pattern: bool,
) -> list[ActionRecommendation]:
    """
    R7: Charge matches recurring pattern → CREATE_CASE + VERIFY + WARN.
    Do NOT block.
    """
    if not is_recurring_pattern:
        return []

    return [
        _action(ActionType.CREATE_CASE.value, 0, "R7: disputed charge matches recurring pattern"),
        _action(ActionType.VERIFY_WITH_CUSTOMER.value, 0, "R7: verify recurring charge with customer"),
        _action(ActionType.WARN_CUSTOMER.value, 0, "R7: send recurring charge reminder"),
    ]


# ── R8: Escalate when uncertain and exposed ───────────────────────────────────

def apply_R8(
    verdict: Verdict,
    exposure_usd: float,
    evidence_conflicts: bool = False,
) -> list[ActionRecommendation]:
    """
    R8: If uncertain AND (exposure > $500 OR evidence conflicts) → ESCALATE.
    """
    if verdict != Verdict.UNCERTAIN:
        return []

    if exposure_usd > config.ESCALATION_EXPOSURE_THRESHOLD or evidence_conflicts:
        reason_parts = []
        if exposure_usd > config.ESCALATION_EXPOSURE_THRESHOLD:
            reason_parts.append(f"exposure ${exposure_usd:.2f} exceeds ${config.ESCALATION_EXPOSURE_THRESHOLD:.0f}")
        if evidence_conflicts:
            reason_parts.append("evidence conflicts")
        return [
            _action(
                ActionType.ESCALATE_TO_ANALYST.value, 0,
                f"R8: uncertain verdict with {'; '.join(reason_parts)}",
            ),
        ]

    return []


# ── R9: Undocumented patterns ─────────────────────────────────────────────────

def apply_R9(
    is_undocumented: bool,
    exposure_usd: float,
) -> list[ActionRecommendation]:
    """
    R9: Coordinated/repeated abuse not matching known patterns →
    CREATE_CASE + FILE_REPORT + ESCALATE.
    """
    if not is_undocumented:
        return []

    return [
        _action(ActionType.CREATE_CASE.value, 0, "R9: undocumented fraud pattern detected"),
        _action(ActionType.FILE_REPORT.value, exposure_usd, "R9: undocumented coordinated abuse"),
        _action(ActionType.ESCALATE_TO_ANALYST.value, 0, "R9: escalate undocumented pattern for analysis"),
    ]


# ── R10: Never BLOCK_ALL_CARDS unless two cards confirmed ─────────────────────

def should_block_all_cards(
    confirmed_fraud_card_count: int,
    credentials_compromised: bool,
) -> bool:
    """
    R10: BLOCK_ALL_CARDS only if ≥2 cards show confirmed fraud OR
    credentials are confirmed compromised.
    """
    return confirmed_fraud_card_count >= 2 or credentials_compromised


# ── Policy §3a: Case vs Report decision ───────────────────────────────────────

def should_create_case(
    fraud_probability: float,
    evidence_requested: bool,
    customer_disputed: bool,
) -> bool:
    """
    §3a: Open a case when fraud_probability ≥ 0.30, or evidence was
    requested, or customer disputes.
    """
    return (
        fraud_probability >= config.CASE_CREATION_THRESHOLD
        or evidence_requested
        or customer_disputed
    )


def should_file_report(
    fraud_confirmed_or_strong: bool,
    exposure_usd: float,
    has_shared_device: bool,
    has_shared_region: bool,
    has_connected_fraud: bool,
    is_undocumented: bool,
) -> bool:
    """
    §3a: FILE_REPORT when fraud confirmed/strongly suspected AND at least
    one condition holds: exposure > $1,000, shared device/region/connected
    fraud, or undocumented/coordinated pattern.
    """
    if not fraud_confirmed_or_strong:
        return False

    return (
        exposure_usd > config.SAR_EXPOSURE_THRESHOLD
        or has_shared_device
        or has_shared_region
        or has_connected_fraud
        or is_undocumented
    )


# ── Policy §6: Stop conditions ────────────────────────────────────────────────

def should_stop(
    fraud_probability: float,
    independent_evidence_count: int,
    verification_settled: bool,
    further_steps_useful: bool,
) -> tuple[bool, str]:
    """
    §6: Stop investigating when one of these holds:
    1. Probability ≥ 0.85 or ≤ 0.15 with ≥2 independent evidence pieces
    2. Verification response settles the question
    3. Further steps unlikely to change decision

    Returns (should_stop, stop_reason).
    """
    if verification_settled:
        return True, "Verification response settled the question"

    if not further_steps_useful:
        return True, "Further steps are unlikely to change the decision"

    if (fraud_probability >= config.CONFIDENCE_THRESHOLD_HIGH
            and independent_evidence_count >= config.MIN_INDEPENDENT_EVIDENCE):
        return True, (
            f"Fraud probability {fraud_probability:.2f} ≥ {config.CONFIDENCE_THRESHOLD_HIGH} "
            f"with {independent_evidence_count} independent evidence pieces"
        )

    if (fraud_probability <= config.CONFIDENCE_THRESHOLD_LOW
            and independent_evidence_count >= config.MIN_INDEPENDENT_EVIDENCE):
        return True, (
            f"Fraud probability {fraud_probability:.2f} ≤ {config.CONFIDENCE_THRESHOLD_LOW} "
            f"with {independent_evidence_count} independent evidence pieces"
        )

    return False, ""


# ── Composite action recommender ──────────────────────────────────────────────

def recommend_actions(state: CaseState) -> list[ActionRecommendation]:
    """
    Apply all relevant policy rules to the current case state and return
    a deduplicated, ordered list of action recommendations.
    """
    actions: list[ActionRecommendation] = []

    # Determine context flags
    customer_denied = any(
        er.type == "customer_validation" and "denied" in er.assumed_response.lower()
        for er in state.evidence_requests
    )
    customer_confirmed = any(
        er.type == "customer_validation" and "confirmed" in er.assumed_response.lower()
        for er in state.evidence_requests
    )
    no_reply = any(
        er.type == "customer_validation" and "no reply" in er.assumed_response.lower()
        for er in state.evidence_requests
    )
    has_shared_device = len(state.connected_device_profiles) > 0
    has_connected_fraud = len(state.connected_card_ids) > 0
    is_card_testing = state.pattern == FraudPattern.CARD_TESTING
    is_undocumented = state.pattern == FraudPattern.UNDOCUMENTED
    evidence_count = len(state.evidence)

    # R3 takes precedence — customer confirms means legitimate
    if customer_confirmed:
        return apply_R3(True)

    # R5: Card testing
    if is_card_testing:
        large_cleared = state.exposure_usd > config.CARD_TESTING_CLEARED_THRESHOLD
        actions.extend(apply_R5(True, large_cleared, state.exposure_usd))

    # R1: Verify before blocking on weak signal
    if not customer_denied and not is_card_testing:
        actions.extend(apply_R1(state.fraud_probability, evidence_count))

    # R2: Customer denies
    if customer_denied:
        actions.extend(apply_R2(True, state.exposure_usd, has_shared_device, has_connected_fraud))

    # R4: No reply
    if no_reply:
        actions.extend(apply_R4(True, state.exposure_usd))

    # R6: Shared origin
    if has_shared_device and has_connected_fraud:
        shared_elem = state.connected_device_profiles[0] if state.connected_device_profiles else None
        actions.extend(apply_R6(shared_elem, "device profile", state.exposure_usd))

    # R7: Disputed but recurring
    # (This would need merchant recurrence detection — handled by the LLM assessment)

    # R8: Escalate when uncertain
    actions.extend(apply_R8(state.verdict, state.exposure_usd))

    # R9: Undocumented pattern
    if is_undocumented:
        actions.extend(apply_R9(True, state.exposure_usd))

    # §3a: Always create a case if threshold met
    if should_create_case(
        state.fraud_probability,
        len(state.evidence_requests) > 0,
        customer_denied,
    ):
        if not any(a.action == ActionType.CREATE_CASE.value for a in actions):
            actions.append(_action(ActionType.CREATE_CASE.value, 0,
                                   f"§3a: fraud probability {state.fraud_probability:.2f} ≥ {config.CASE_CREATION_THRESHOLD}"))

    # §3a: File report if conditions met
    fraud_strong = state.fraud_probability >= 0.70
    if should_file_report(
        fraud_strong, state.exposure_usd,
        has_shared_device, False, has_connected_fraud, is_undocumented,
    ):
        if not any(a.action == ActionType.FILE_REPORT.value for a in actions):
            actions.append(_action(ActionType.FILE_REPORT.value, state.exposure_usd,
                                   "§3a: confirmed/strongly suspected fraud meets filing criteria"))

    # If fraud confirmed and no block yet, consider it
    if state.verdict == Verdict.FRAUD and state.fraud_probability >= 0.70:
        if not any(a.action in (ActionType.BLOCK_CARD.value, ActionType.BLOCK_ALL_CARDS.value) for a in actions):
            actions.append(_action(ActionType.BLOCK_CARD.value, state.exposure_usd,
                                   "Fraud confirmed with high probability"))

    # If legitimate with decent confidence, close
    if state.verdict == Verdict.LEGITIMATE and state.fraud_probability <= 0.30:
        if not any(a.action == ActionType.CLOSE_NO_FRAUD.value for a in actions):
            actions.append(_action(ActionType.CLOSE_NO_FRAUD.value, 0,
                                   "Legitimate transaction — fraud probability below threshold"))

    # If legitimate but moderate probability, monitor
    if state.verdict == Verdict.LEGITIMATE and 0.15 < state.fraud_probability <= 0.30:
        if not any(a.action in (ActionType.MONITOR_CARD.value, ActionType.CLOSE_NO_FRAUD.value) for a in actions):
            actions.append(_action(ActionType.MONITOR_CARD.value, 0,
                                   "Legitimate but moderate risk — monitor"))
            actions.append(_action(ActionType.CLOSE_NO_FRAUD.value, 0,
                                   "Legitimate transaction"))

    # Deduplicate by action name, keep first occurrence
    seen: set[str] = set()
    deduped: list[ActionRecommendation] = []
    for a in actions:
        if a.action not in seen:
            seen.add(a.action)
            deduped.append(a)

    return deduped
