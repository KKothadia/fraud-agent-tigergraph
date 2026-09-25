"""
Gather More Evidence node — request additional evidence and simulate responses.

Implements the evidence-request loop described in Policy §5.
The agent may request: customer_validation, step_up_auth, or analyst_info.
Responses are simulated since actual responses are not provided.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.state import CaseState, EvidenceItem, EvidenceRequest
from agent.prompts.templates import SIMULATE_RESPONSE_SYSTEM_PROMPT, SIMULATE_RESPONSE_USER_PROMPT
from agent import config

logger = logging.getLogger(__name__)


def _simulate_response(state: CaseState, request_type: str) -> str:
    """
    Simulate a realistic response to an evidence request.
    Uses the LLM if available, otherwise uses deterministic logic.
    """
    # Try LLM simulation
    if config.OPENROUTER_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=config.OPENROUTER_API_KEY,
            )

            evidence_summary = "\n".join(
                f"- {e.claim}" for e in state.evidence[:8]
            )

            flagged = state._graph_context.get("flagged_txn", {})
            amount = flagged.get("TransactionAmt", 0)

            prompt = SIMULATE_RESPONSE_USER_PROMPT.format(
                case_id=state.case_id,
                request_type=request_type,
                fraud_probability=f"{state.fraud_probability:.2f}",
                pattern=state.pattern.value,
                flagged_txn_id=state.trigger.flagged_txn_id if state.trigger else "",
                amount=f"{amount:.2f}",
                evidence_summary=evidence_summary,
            )

            response = client.chat.completions.create(
                model=config.LLM_MODEL,
                max_tokens=500,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": SIMULATE_RESPONSE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"LLM simulation failed: {e}")

    # Deterministic fallback
    return _deterministic_response(state, request_type)


def _deterministic_response(state: CaseState, request_type: str) -> str:
    """Generate a realistic simulated response based on case context."""
    prob = state.fraud_probability
    trigger_type = state.trigger.trigger_type.value if state.trigger else ""

    if request_type == "customer_validation":
        if trigger_type == "customer_report":
            # Customer already reported it — they will deny
            return (
                "Customer states they did not make this purchase and "
                "did not authorize anyone to use their card. "
                "Customer still has the physical card in their possession."
            )
        elif prob >= 0.65:
            return (
                "Customer states they do not recognize this transaction "
                "and did not make this purchase. Customer requests the "
                "card be blocked immediately."
            )
        elif prob >= 0.40:
            # Ambiguous — simulate a no-reply for uncertainty
            return (
                "No reply received from customer within the 24-hour "
                "verification window."
            )
        else:
            return (
                "Customer confirmed they made this purchase. It was "
                "a legitimate transaction for a product they ordered online."
            )

    elif request_type == "step_up_auth":
        if prob >= 0.60:
            return (
                "Step-up authentication failed: the user could not "
                "complete the one-time passcode verification. "
                "Three attempts were made without success."
            )
        else:
            return (
                "Step-up authentication succeeded: the cardholder "
                "completed the one-time passcode verification successfully."
            )

    elif request_type == "analyst_info":
        # Provide context from similar cases
        if state._closed_cases:
            similar = state._closed_cases[0]
            return (
                f"Analyst reviewed related case {similar.get('case_id', 'N/A')}: "
                f"outcome was {similar.get('outcome', 'unknown')} "
                f"({similar.get('pattern', 'no pattern')}). "
                f"Analyst notes: similar activity pattern observed."
            )
        return (
            "Analyst reviewed the account history and found no "
            "additional indicators of compromise beyond what was "
            "already identified."
        )

    return "No additional information available."


def run_gather_more(state: CaseState) -> CaseState:
    """
    Request additional evidence and incorporate the simulated response.
    This node is called when the assessment determines more evidence is needed.
    """
    logger.info(f"[{state.case_id}] Gathering more evidence (loop {state.evidence_loop_count + 1})")

    # Determine what type of evidence to request
    suggested = state._graph_context.get("suggested_evidence_request")

    if not suggested:
        # Default based on context
        if state.trigger and state.trigger.trigger_type.value == "customer_report":
            suggested = "customer_validation"
        elif state.fraud_probability < 0.70:
            suggested = "customer_validation"  # R1: verify before blocking
        else:
            suggested = "step_up_auth"

    # Don't repeat the same request type
    existing_types = {er.type for er in state.evidence_requests}
    if suggested in existing_types:
        # Try alternatives
        alternatives = ["customer_validation", "step_up_auth", "analyst_info"]
        for alt in alternatives:
            if alt not in existing_types:
                suggested = alt
                break
        else:
            # All types exhausted — stop the loop
            state.sufficient_evidence = True
            state.log_decision(
                "GATHER_MORE",
                "All evidence request types exhausted. Proceeding with available evidence."
            )
            return state

    # Simulate the response
    response = _simulate_response(state, suggested)

    # Record the evidence request
    evidence_request = EvidenceRequest(
        type=suggested,
        asked_after_step=state.current_step,
        assumed_response=response,
    )
    state.evidence_requests.append(evidence_request)

    # Add the response as evidence
    state.evidence.append(EvidenceItem(
        claim=response,
        source="customer" if suggested == "customer_validation" else "external",
        ref=f"evidence_request:{len(state.evidence_requests)}",
        entity_ids=[],
    ))

    state.evidence_loop_count += 1

    # Check if loop limit reached
    if state.evidence_loop_count >= config.MAX_EVIDENCE_LOOPS:
        state.log_decision(
            "GATHER_MORE",
            f"Maximum evidence loops ({config.MAX_EVIDENCE_LOOPS}) reached. "
            f"Proceeding with available evidence."
        )
    else:
        state.log_decision(
            "GATHER_MORE",
            f"Requested {suggested}. Response: {response[:100]}..."
        )

    logger.info(
        f"[{state.case_id}] Evidence request: {suggested}, "
        f"loop {state.evidence_loop_count}/{config.MAX_EVIDENCE_LOOPS}"
    )

    return state
