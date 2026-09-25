"""
Assess node — use the LLM to synthesize evidence into a fraud assessment.

The LLM is constrained to cite only the evidence provided. Every claim
must reference evidence IDs. The output is structured (JSON) and
validated before being applied to the case state.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from agent.state import (
    CaseState,
    Verdict,
    FraudPattern,
    CaseStatus,
)
from agent.prompts.templates import ASSESS_SYSTEM_PROMPT, ASSESS_USER_PROMPT
from agent import config

logger = logging.getLogger(__name__)


def _build_evidence_context(state: CaseState) -> dict[str, str]:
    """Build the context strings for the assessment prompt."""
    # Flagged transaction details
    flagged = state._graph_context.get("flagged_txn", {})
    flagged_str = json.dumps({
        k: v for k, v in flagged.items()
        if k in ("TransactionID", "TransactionAmt", "ProductCD", "ts",
                  "channel", "risk_score", "addr1", "addr2",
                  "P_emaildomain", "R_emaildomain")
    }, indent=2, default=str) if flagged else "Not available"

    # Card history
    history = state._graph_context.get("card_history_recent", [])
    history_str = json.dumps(history[:10], indent=2, default=str) if history else "No history available"

    # Device info
    device = state._device_info
    device_str = json.dumps({
        k: v for k, v in device.items()
        if k in ("has_identity", "DeviceType", "DeviceInfo", "id_15", "id_23",
                  "id_30", "id_31", "id_33", "device_profile_string")
    }, indent=2, default=str) if device else "No device record"

    # Customer profile
    profile_str = json.dumps(state._customer_profile, indent=2, default=str) if state._customer_profile else "Not available"

    # Pattern results
    pattern_results = state._graph_context.get("pattern_results", [])
    pattern_str = json.dumps([
        {k: v for k, v in p.items() if k != "note"}
        for p in pattern_results
        if p.get("matched") or p.get("confidence", 0) > 0.1
    ], indent=2, default=str) if pattern_results else "No patterns detected"

    # Similar cases
    similar = state._closed_cases
    similar_str = json.dumps(similar[:5], indent=2, default=str) if similar else "No similar cases found"

    # Evidence requests
    er_str = "None"
    if state.evidence_requests:
        er_str = json.dumps([
            {"type": er.type, "response": er.assumed_response}
            for er in state.evidence_requests
        ], indent=2)

    return {
        "flagged_txn_details": flagged_str,
        "card_history": history_str,
        "device_info": device_str,
        "customer_profile": profile_str,
        "pattern_results": pattern_str,
        "similar_cases": similar_str,
        "evidence_requests": er_str,
    }


def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """Call the LLM and return the response text."""
    try:
        from openai import OpenAI
        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=config.OPENROUTER_API_KEY,
        )
        response = client.chat.completions.create(
            model=config.LLM_MODEL,
            max_tokens=config.LLM_MAX_TOKENS,
            temperature=config.LLM_TEMPERATURE,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
        )
        return response.choices[0].message.content
    except ImportError:
        logger.warning("OpenAI SDK not installed — using fallback assessment")
        return ""
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return ""


def _parse_llm_assessment(response_text: str) -> dict[str, Any]:
    """Parse the structured JSON from the LLM response."""
    if not response_text:
        return {}

    # Try to extract JSON from the response
    # Handle markdown code blocks
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response_text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find raw JSON
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            return {}

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM JSON: {e}")
        return {}


def _fallback_assessment(state: CaseState) -> dict[str, Any]:
    """
    Deterministic assessment fallback when LLM is unavailable.
    Uses pattern matches, evidence count, and heuristics.
    """
    # Determine best pattern match
    best_pattern = None
    best_confidence = 0.0
    if state.patterns_matched:
        best = max(state.patterns_matched, key=lambda p: p.confidence)
        best_pattern = best.pattern
        best_confidence = best.confidence

    # Base fraud probability on evidence
    prob = state.fraud_probability  # Start from trigger-based estimate

    # Adjust based on pattern matches
    if best_confidence > 0.5:
        prob = max(prob, best_confidence)

    # Adjust based on similar cases
    fraud_cases = [c for c in state._closed_cases if c.get("outcome") == "confirmed_fraud"]
    if fraud_cases:
        prob += 0.10 * min(len(fraud_cases), 3)

    cleared_cases = [c for c in state._closed_cases if c.get("outcome") == "cleared"]
    if cleared_cases:
        prob -= 0.05 * min(len(cleared_cases), 2)

    # Customer report increases probability
    if state.trigger and state.trigger.trigger_type.value == "customer_report":
        prob += 0.10

    # Customer denial is strong signal
    if any("denied" in er.assumed_response.lower() for er in state.evidence_requests):
        prob += 0.20

    # Customer confirmation settles it
    if any("confirmed" in er.assumed_response.lower() for er in state.evidence_requests):
        prob = max(0.05, prob - 0.50)

    # Device New + proxy is suspicious
    if state._device_info.get("id_15") == "New":
        prob += 0.10
    if state._device_info.get("id_23") in ("anonymous", "hidden"):
        prob += 0.10

    prob = max(0.0, min(1.0, prob))

    # Determine verdict
    if prob >= 0.70:
        verdict = "fraud"
    elif prob <= 0.30:
        verdict = "legitimate"
    else:
        verdict = "uncertain"

    # Determine pattern
    pattern = best_pattern.value if best_pattern else "none"
    affected_txn_ids = []
    if best_pattern and state.patterns_matched:
        best_match = max(state.patterns_matched, key=lambda p: p.confidence)
        affected_txn_ids = best_match.evidence_txn_ids

    if not affected_txn_ids and verdict == "fraud" and state.trigger:
        affected_txn_ids = [state.trigger.flagged_txn_id]

    # Calculate exposure
    exposure = 0.0
    if affected_txn_ids and state._card_history:
        for txn in state._card_history:
            if str(txn.get("TransactionID", "")) in affected_txn_ids:
                exposure += abs(float(txn.get("TransactionAmt", 0)))

    if exposure == 0 and affected_txn_ids:
        flagged = state._graph_context.get("flagged_txn", {})
        exposure = abs(float(flagged.get("TransactionAmt", 0)))

    return {
        "verdict": verdict,
        "fraud_probability": round(prob, 2),
        "pattern": pattern,
        "pattern_description": "",
        "affected_txn_ids": affected_txn_ids,
        "first_suspicious_txn_id": affected_txn_ids[0] if affected_txn_ids else "",
        "connected_card_ids": state.connected_card_ids,
        "connected_device_profiles": state.connected_device_profiles,
        "exposure_usd": round(exposure, 2),
        "confidence_reasoning": f"Based on {len(state.evidence)} evidence items, "
                                f"{len(state.patterns_matched)} pattern matches, "
                                f"{len(state.similar_prior_cases)} similar cases.",
        "needs_more_evidence": 0.30 < prob < 0.70 and state.evidence_loop_count < config.MAX_EVIDENCE_LOOPS,
        "suggested_evidence_request": "customer_validation" if 0.30 < prob < 0.70 else None,
    }


def run_assess(state: CaseState) -> CaseState:
    """
    Assess the case using the LLM (or fallback heuristic).
    Updates state with verdict, fraud probability, pattern, affected txns,
    and exposure.
    """
    logger.info(f"[{state.case_id}] Running assessment (loop {state.evidence_loop_count})")

    # Try LLM assessment first
    assessment = {}
    if config.OPENROUTER_API_KEY:
        context = _build_evidence_context(state)
        user_prompt = ASSESS_USER_PROMPT.format(
            case_id=state.case_id,
            trigger_type=state.trigger.trigger_type.value if state.trigger else "",
            trigger_text=state.trigger.trigger_text if state.trigger else "",
            flagged_txn_id=state.trigger.flagged_txn_id if state.trigger else "",
            card_id=state.trigger.card_id if state.trigger else "",
            customer_id=state.trigger.customer_id if state.trigger else "",
            risk_score=state.trigger.risk_score if state.trigger else "N/A",
            **context,
        )

        response = _call_llm(ASSESS_SYSTEM_PROMPT, user_prompt)
        assessment = _parse_llm_assessment(response)

    # Fallback to heuristic if LLM failed or unavailable
    if not assessment:
        assessment = _fallback_assessment(state)

    # Apply assessment to state
    try:
        state.verdict = Verdict(assessment.get("verdict", "uncertain"))
    except ValueError:
        state.verdict = Verdict.UNCERTAIN

    state.fraud_probability = float(assessment.get("fraud_probability", state.fraud_probability))

    try:
        state.pattern = FraudPattern(assessment.get("pattern", "none"))
    except ValueError:
        state.pattern = FraudPattern.NONE

    state.pattern_description = assessment.get("pattern_description", "")

    # Affected transactions
    new_affected = assessment.get("affected_txn_ids", [])
    if new_affected:
        # Ensure they're strings
        state.affected_txn_ids = [str(t) for t in new_affected]

    state.first_suspicious_txn_id = str(assessment.get("first_suspicious_txn_id", ""))

    # Connected entities
    new_connected_cards = assessment.get("connected_card_ids", [])
    if new_connected_cards:
        state.connected_card_ids = list(set(state.connected_card_ids + [str(c) for c in new_connected_cards]))

    new_devices = assessment.get("connected_device_profiles", [])
    if new_devices:
        state.connected_device_profiles = list(set(state.connected_device_profiles + [str(d) for d in new_devices]))

    state.exposure_usd = float(assessment.get("exposure_usd", state.exposure_usd))

    # Check if more evidence is needed
    needs_more = assessment.get("needs_more_evidence", False)
    suggested_request = assessment.get("suggested_evidence_request")

    # Apply stop conditions
    from agent.policy.rules import should_stop
    evidence_count = len([e for e in state.evidence if e.source == "graph"])
    verification_settled = any(
        er.type == "customer_validation"
        for er in state.evidence_requests
    )

    stop, stop_reason = should_stop(
        state.fraud_probability,
        evidence_count,
        verification_settled,
        not needs_more,
    )

    state.sufficient_evidence = stop
    if stop:
        state.stop_reason = stop_reason

    # Update case status
    if state.verdict == Verdict.FRAUD:
        state.status = CaseStatus.CLOSED_FRAUD
    elif state.verdict == Verdict.LEGITIMATE:
        state.status = CaseStatus.CLOSED_LEGITIMATE
    elif stop:
        if state.fraud_probability >= 0.5:
            state.status = CaseStatus.ESCALATED
        else:
            state.status = CaseStatus.CLOSED_LEGITIMATE

    # Store suggestion for the gather_more node
    state._graph_context["suggested_evidence_request"] = suggested_request

    state.log_decision(
        "ASSESS",
        f"Verdict: {state.verdict.value}, probability: {state.fraud_probability:.2f}, "
        f"pattern: {state.pattern.value}, exposure: ${state.exposure_usd:.2f}. "
        f"Sufficient evidence: {state.sufficient_evidence}. "
        f"Stop reason: {state.stop_reason or 'N/A'}. "
        f"Confidence threshold: high={config.CONFIDENCE_THRESHOLD_HIGH}, "
        f"low={config.CONFIDENCE_THRESHOLD_LOW}."
    )

    logger.info(
        f"[{state.case_id}] Assessment: {state.verdict.value} "
        f"(p={state.fraud_probability:.2f}), pattern={state.pattern.value}"
    )

    return state
