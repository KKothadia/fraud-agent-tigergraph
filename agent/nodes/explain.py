"""
Explain node — generate the case summary and SAR narrative.

Produces:
1. Case summary (2-6 sentences for the analyst)
2. SAR narrative (6-12 sentences for the regulator, when FILE_REPORT is recommended)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from agent.state import CaseState, SARReport, ActionType
from agent.prompts.templates import (
    EXPLAIN_SYSTEM_PROMPT,
    EXPLAIN_USER_PROMPT,
    SAR_SYSTEM_PROMPT,
    SAR_USER_PROMPT,
)
from agent import config

logger = logging.getLogger(__name__)


def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """Call the LLM and return the response text."""
    if not config.OPENROUTER_API_KEY:
        return ""
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
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"LLM call failed: {e}")
        return ""


def _generate_fallback_summary(state: CaseState) -> str:
    """Generate a deterministic case summary when LLM is unavailable."""
    parts = []

    # Opening
    trigger_desc = ""
    if state.trigger:
        if state.trigger.trigger_type.value == "customer_report":
            trigger_desc = f"Customer {state.trigger.customer_id} reported an unrecognized transaction"
        elif state.trigger.trigger_type.value == "risk_score":
            trigger_desc = (
                f"Risk model flagged transaction {state.trigger.flagged_txn_id} "
                f"at score {state.trigger.risk_score}"
            )
        else:
            trigger_desc = f"Analyst requested review of transaction {state.trigger.flagged_txn_id}"

    flagged = state._graph_context.get("flagged_txn", {})
    amount = flagged.get("TransactionAmt", 0)
    channel = flagged.get("channel", "unknown")
    parts.append(
        f"{trigger_desc} (${amount:.2f}, {channel}) "
        f"on card {state.trigger.card_id if state.trigger else 'unknown'}."
    )

    # Pattern
    if state.pattern.value != "none":
        parts.append(
            f"Investigation identified pattern '{state.pattern.value}' "
            f"with {len(state.affected_txn_ids)} affected transaction(s) "
            f"totaling ${state.exposure_usd:.2f}."
        )

    # Key evidence
    graph_evidence = [e for e in state.evidence if e.source == "graph"]
    if graph_evidence:
        parts.append(
            f"Key evidence: {graph_evidence[0].claim}"
        )

    # Similar cases
    if state.similar_prior_cases:
        parts.append(
            f"Similar prior case(s) retrieved: {', '.join(state.similar_prior_cases[:3])}."
        )

    # Evidence requests
    if state.evidence_requests:
        for er in state.evidence_requests:
            parts.append(f"Evidence requested ({er.type}): {er.assumed_response[:80]}")

    # Verdict
    parts.append(
        f"Verdict: {state.verdict.value} "
        f"(fraud probability {state.fraud_probability:.2f})."
    )

    return " ".join(parts[:6])  # Cap at 6 sentences


def _generate_fallback_sar(state: CaseState) -> str:
    """Generate a deterministic SAR narrative when LLM is unavailable."""
    flagged = state._graph_context.get("flagged_txn", {})
    ts = flagged.get("ts", "unknown date")
    amount = flagged.get("TransactionAmt", 0)
    channel = flagged.get("channel", "unknown")
    customer_id = state.trigger.customer_id if state.trigger else "unknown"
    card_id = state.trigger.card_id if state.trigger else "unknown"

    narrative_parts = [
        f"On {ts}, card {card_id} belonging to customer {customer_id} "
        f"was used for a {channel} transaction of ${amount:.2f}.",
    ]

    if state.pattern.value != "none":
        narrative_parts.append(
            f"Investigation identified this as consistent with the "
            f"'{state.pattern.value}' fraud pattern."
        )

    if state.affected_txn_ids:
        narrative_parts.append(
            f"A total of {len(state.affected_txn_ids)} transaction(s) were identified "
            f"as part of this fraud episode, totaling ${state.exposure_usd:.2f}."
        )

    if state.connected_card_ids:
        narrative_parts.append(
            f"Additional cards were identified as connected to this compromise: "
            f"{', '.join(state.connected_card_ids[:3])}."
        )

    if state.connected_device_profiles:
        narrative_parts.append(
            f"The following device profile(s) link this activity: "
            f"{'; '.join(state.connected_device_profiles[:2])}."
        )

    for er in state.evidence_requests:
        if er.type == "customer_validation":
            narrative_parts.append(f"Customer response: {er.assumed_response}")

    actions_desc = ", ".join(a.action for a in state.next_best_actions.final)
    narrative_parts.append(f"Recommended actions: {actions_desc}.")

    return " ".join(narrative_parts)


def run_explain(state: CaseState) -> CaseState:
    """
    Generate the case summary and SAR narrative (if FILE_REPORT is recommended).
    """
    logger.info(f"[{state.case_id}] Generating explanation")

    # ── Case summary ───────────────────────────────────────────────────────
    evidence_list = "\n".join(
        f"- [{e.source}] {e.claim} (ref: {e.ref})"
        for e in state.evidence
    )
    actions_summary = ", ".join(
        f"{a.action}({a.route})" for a in state.next_best_actions.final
    )
    rules_applied = ", ".join(set(
        a.reason.split(":")[0].strip()
        for a in state.next_best_actions.final
    ))

    summary = _call_llm(
        EXPLAIN_SYSTEM_PROMPT,
        EXPLAIN_USER_PROMPT.format(
            case_id=state.case_id,
            verdict=state.verdict.value,
            fraud_probability=f"{state.fraud_probability:.2f}",
            pattern=state.pattern.value,
            affected_txn_ids=str(state.affected_txn_ids),
            exposure_usd=f"{state.exposure_usd:.2f}",
            evidence_count=len(state.evidence),
            evidence_request_count=len(state.evidence_requests),
            actions_summary=actions_summary,
            rules_applied=rules_applied,
            evidence_list=evidence_list,
        ),
    )

    if not summary:
        summary = _generate_fallback_summary(state)

    state.summary = summary

    # ── SAR narrative ──────────────────────────────────────────────────────
    file_report = any(
        a.action == ActionType.FILE_REPORT.value
        for a in state.next_best_actions.final
    )

    if file_report:
        # Get activity dates
        activity_dates = []
        if state.affected_txn_ids and state._card_history:
            affected_ts = []
            for txn in state._card_history:
                if str(txn.get("TransactionID", "")) in state.affected_txn_ids:
                    ts = txn.get("ts", "")
                    if ts:
                        affected_ts.append(str(ts)[:10])
            if affected_ts:
                activity_dates = [min(affected_ts), max(affected_ts)]

        if not activity_dates:
            flagged = state._graph_context.get("flagged_txn", {})
            ts = str(flagged.get("ts", ""))[:10]
            if ts:
                activity_dates = [ts, ts]

        # Subjects
        subjects = []
        if state.trigger:
            subjects.append(state.trigger.customer_id)
            subjects.append(state.trigger.card_id)
        subjects.extend(state.connected_card_ids[:3])
        subjects = list(dict.fromkeys(subjects))  # Deduplicate preserving order

        # Generate narrative
        connected_entities = (
            f"Connected cards: {', '.join(state.connected_card_ids)}. "
            f"Connected devices: {', '.join(state.connected_device_profiles)}"
            if state.connected_card_ids or state.connected_device_profiles
            else "None identified"
        )

        prior_cases_str = (
            "\n".join(f"- {c}" for c in state.similar_prior_cases[:3])
            if state.similar_prior_cases else "None"
        )

        narrative = _call_llm(
            SAR_SYSTEM_PROMPT,
            SAR_USER_PROMPT.format(
                case_id=state.case_id,
                customer_id=state.trigger.customer_id if state.trigger else "",
                card_ids=state.trigger.card_id if state.trigger else "",
                pattern=state.pattern.value,
                total_amount_usd=f"{state.exposure_usd:.2f}",
                activity_start=activity_dates[0] if activity_dates else "unknown",
                activity_end=activity_dates[-1] if activity_dates else "unknown",
                evidence_list=evidence_list,
                connected_entities=connected_entities,
                actions_taken=actions_summary,
                prior_cases=prior_cases_str,
            ),
        )

        if not narrative:
            narrative = _generate_fallback_sar(state)

        # Determine SAR reason
        sar_reasons = [
            a.reason for a in state.next_best_actions.final
            if a.action == ActionType.FILE_REPORT.value
        ]

        state.sar = SARReport(
            file=True,
            reason=sar_reasons[0] if sar_reasons else "Fraud confirmed and filing criteria met",
            narrative=narrative,
            subjects=subjects,
            total_amount_usd=state.exposure_usd,
            activity_dates=activity_dates,
        )
    else:
        # No SAR needed — document why
        reason = (
            f"Filing not required: verdict is {state.verdict.value} "
            f"with fraud probability {state.fraud_probability:.2f}"
        )
        if state.verdict.value == "legitimate":
            reason += ". Activity determined to be legitimate."
        elif state.exposure_usd <= config.SAR_EXPOSURE_THRESHOLD and not state.connected_device_profiles:
            reason += f". Exposure ${state.exposure_usd:.2f} below threshold and no shared device links."

        state.sar = SARReport(
            file=False,
            reason=reason,
            narrative="",
            subjects=[],
            total_amount_usd=0,
            activity_dates=[],
        )

    state.log_decision(
        "EXPLAIN",
        f"Summary generated ({len(state.summary)} chars). "
        f"SAR: {'filed' if state.sar.file else 'not filed'} — {state.sar.reason[:80]}."
    )

    logger.info(f"[{state.case_id}] Explanation complete. SAR: {state.sar.file}")

    return state
