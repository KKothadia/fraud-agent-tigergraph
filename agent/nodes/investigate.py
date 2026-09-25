"""
Investigate node — run GSQL-equivalent queries to pull the transaction's
full context: neighborhood, card history, device info, pattern checks,
and similar closed cases.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.state import CaseState, EvidenceItem, PatternMatch, FraudPattern
from agent.tools.tigergraph_tools import DataLayer

logger = logging.getLogger(__name__)


def run_investigate(state: CaseState, data: DataLayer) -> CaseState:
    """
    Pull all graph-derived evidence for the flagged transaction.
    This runs the equivalent of GSQL neighborhood traversal,
    pattern detection, and case memory retrieval.
    """
    if not state.trigger:
        raise ValueError("Trigger must be set before investigation")

    txn_id = state.trigger.flagged_txn_id
    card_id = state.trigger.card_id
    customer_id = state.trigger.customer_id

    logger.info(f"[{state.case_id}] Investigating txn {txn_id} on card {card_id}")

    # ── 1. Get flagged transaction details ─────────────────────────────────
    txn_details = data.get_transaction(txn_id)
    if "error" not in txn_details:
        state.transactions_examined.append(txn_id)
        state._graph_context["flagged_txn"] = txn_details
        state.evidence.append(EvidenceItem(
            claim=f"Flagged transaction {txn_id}: ${txn_details.get('TransactionAmt', 0):.2f}, "
                  f"channel={txn_details.get('channel', 'unknown')}, "
                  f"product={txn_details.get('ProductCD', '?')}, "
                  f"risk_score={txn_details.get('risk_score', '?')}",
            source="graph",
            ref=f"query:get_transaction(txn_id={txn_id})",
            entity_ids=[txn_id],
        ))

    # ── 2. Get customer profile ────────────────────────────────────────────
    profile = data.get_customer_profile(customer_id)
    state._customer_profile = profile
    state.entities_examined.append(customer_id)

    if "error" not in profile:
        state.evidence.append(EvidenceItem(
            claim=f"Customer {customer_id}: {profile.get('total_transactions', 0)} total txns, "
                  f"avg ${profile.get('avg_amount', 0):.2f}, "
                  f"active {profile.get('first_seen', '?')} to {profile.get('last_seen', '?')}, "
                  f"primary region {profile.get('primary_region', '?')}",
            source="graph",
            ref=f"query:get_customer_profile(customer_id={customer_id})",
            entity_ids=[customer_id],
        ))

    # ── 3. Get card transaction history ────────────────────────────────────
    card_history = data.get_card_history(card_id)
    state._card_history = card_history
    state.entities_examined.append(card_id)

    if card_history:
        recent = card_history[-10:]  # Last 10 txns for context
        state._graph_context["card_history_recent"] = recent
        amounts_str = ", ".join(f"${t.get('TransactionAmt', 0):.2f}" for t in recent)
        state.evidence.append(EvidenceItem(
            claim=f"Card {card_id}: {len(card_history)} total transactions. "
                  f"Last 10 amounts: [{amounts_str}]",
            source="graph",
            ref=f"query:get_card_history(card_id={card_id})",
            entity_ids=[card_id],
        ))

    # ── 4. Get device info ─────────────────────────────────────────────────
    device_info = data.get_device_info(txn_id)
    state._device_info = device_info

    if device_info.get("has_identity"):
        profile_str = device_info.get("device_profile_string", "")
        device_new = device_info.get("id_15", "")
        proxy = device_info.get("id_23", "")

        state.evidence.append(EvidenceItem(
            claim=f"Device profile for txn {txn_id}: {profile_str}. "
                  f"Device status: {device_new}. Proxy: {proxy or 'none'}",
            source="graph",
            ref=f"query:get_device_info(txn_id={txn_id})",
            entity_ids=[txn_id],
        ))

        if profile_str:
            state.entities_examined.append(f"device:{profile_str[:50]}")

        # Find other cards sharing this device
        if device_info.get("DeviceInfo"):
            neighbors = data.find_device_neighbors(device_info["DeviceInfo"])
            other_customers = set()
            other_cards = set()
            for n in neighbors:
                if n["customer_id"] != customer_id:
                    other_customers.add(n["customer_id"])
                    # Try to find card IDs
            if other_customers:
                state.evidence.append(EvidenceItem(
                    claim=f"Device '{device_info['DeviceInfo']}' also used by "
                          f"{len(other_customers)} other customer(s): {list(other_customers)[:5]}",
                    source="graph",
                    ref=f"query:find_device_neighbors(device={device_info['DeviceInfo']})",
                    entity_ids=list(other_customers)[:5],
                ))
                state.connected_device_profiles.append(profile_str)

    # ── 5. Run pattern detection ───────────────────────────────────────────
    pattern_results = data.run_all_pattern_checks(card_id, txn_id)
    state._graph_context["pattern_results"] = pattern_results

    for pr in pattern_results:
        if pr.get("matched"):
            pattern_name = pr["pattern"]
            try:
                pattern_enum = FraudPattern(pattern_name)
            except ValueError:
                pattern_enum = FraudPattern.UNDOCUMENTED

            match = PatternMatch(
                pattern=pattern_enum,
                matched=True,
                confidence=pr.get("confidence", 0.0),
                evidence_txn_ids=pr.get("txn_ids", []),
                details=pr,
            )
            state.patterns_matched.append(match)

            indicators = pr.get("indicators", [])
            state.evidence.append(EvidenceItem(
                claim=f"Pattern '{pattern_name}' detected (confidence: {pr.get('confidence', 0):.2f}). "
                      f"Indicators: {'; '.join(indicators) if indicators else 'matched'}",
                source="graph",
                ref=f"query:detect_{pattern_name}(card_id={card_id}, txn_id={txn_id})",
                entity_ids=pr.get("txn_ids", [txn_id]),
            ))

    # ── 6. Find similar closed cases ───────────────────────────────────────
    # Use the strongest matched pattern for retrieval
    best_pattern = ""
    if state.patterns_matched:
        best = max(state.patterns_matched, key=lambda p: p.confidence)
        best_pattern = best.pattern.value

    similar = data.find_similar_closed_cases(
        pattern=best_pattern,
        customer_id=customer_id,
        card_id=card_id,
    )
    state._closed_cases = similar

    for sc in similar:
        state.similar_prior_cases.append(sc["case_id"])
        state.evidence.append(EvidenceItem(
            claim=f"Similar closed case {sc['case_id']}: {sc['outcome']} ({sc['pattern']}), "
                  f"exposure ${sc['exposure_usd']:.2f}. {sc.get('analyst_notes', '')[:100]}",
            source="graph",
            ref=f"query:find_similar_cases(pattern={best_pattern}, customer={customer_id})",
            entity_ids=[sc["case_id"]],
        ))

    state.tool_calls = data.tool_call_count

    state.log_decision(
        "INVESTIGATE",
        f"Examined {len(state.entities_examined)} entities, "
        f"{len(state.transactions_examined)} transactions. "
        f"Found {len(state.patterns_matched)} pattern matches, "
        f"{len(state.similar_prior_cases)} similar prior cases. "
        f"Collected {len(state.evidence)} evidence items."
    )

    logger.info(
        f"[{state.case_id}] Investigation complete: "
        f"{len(state.patterns_matched)} patterns, "
        f"{len(state.evidence)} evidence items"
    )

    return state
