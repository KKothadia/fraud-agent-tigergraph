"""
Case state schema — the data structure carried through every node of the
LangGraph state machine.

Matches the README Answer Format exactly so serialization to the answer JSON
is a direct mapping.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── Enumerations ───────────────────────────────────────────────────────────────

class TriggerType(str, Enum):
    RISK_SCORE = "risk_score"
    CUSTOMER_REPORT = "customer_report"
    ANALYST_REQUEST = "analyst_request"


class CaseStatus(str, Enum):
    OPEN = "open"
    CLOSED_FRAUD = "closed_fraud"
    CLOSED_LEGITIMATE = "closed_legitimate"
    ESCALATED = "escalated"


class Verdict(str, Enum):
    FRAUD = "fraud"
    LEGITIMATE = "legitimate"
    UNCERTAIN = "uncertain"


class FraudPattern(str, Enum):
    CARD_TESTING = "card_testing"
    CARD_NOT_PRESENT_FRAUD = "card_not_present_fraud"
    CARD_NOT_PRESENT_NEW_DEVICE = "card_not_present_new_device"
    OUT_OF_REGION_USE = "out_of_region_use"
    ACCOUNT_TAKEOVER = "account_takeover"
    UNDOCUMENTED = "undocumented"
    NONE = "none"


class ActionType(str, Enum):
    """Policy §1 — exhaustive action whitelist."""
    ALLOW_TRANSACTION = "ALLOW_TRANSACTION"
    DECLINE_TRANSACTION = "DECLINE_TRANSACTION"
    MONITOR_CARD = "MONITOR_CARD"
    MONITOR_CONNECTED_CARDS = "MONITOR_CONNECTED_CARDS"
    WARN_CUSTOMER = "WARN_CUSTOMER"
    VERIFY_WITH_CUSTOMER = "VERIFY_WITH_CUSTOMER"
    STEP_UP_AUTH = "STEP_UP_AUTH"
    BLOCK_CARD = "BLOCK_CARD"
    BLOCK_ALL_CARDS = "BLOCK_ALL_CARDS"
    GENERATE_REPORT = "GENERATE_REPORT"
    CREATE_CASE = "CREATE_CASE"
    FILE_REPORT = "FILE_REPORT"
    ESCALATE_TO_ANALYST = "ESCALATE_TO_ANALYST"
    CLOSE_NO_FRAUD = "CLOSE_NO_FRAUD"


class ApprovalRoute(str, Enum):
    """Policy §2 — approval routing."""
    AUTO = "auto"
    L1 = "L1"
    L2 = "L2"


class EvidenceSource(str, Enum):
    GRAPH = "graph"
    DOCUMENT = "document"
    CUSTOMER = "customer"
    EXTERNAL = "external"


class EvidenceRequestType(str, Enum):
    CUSTOMER_VALIDATION = "customer_validation"
    STEP_UP_AUTH = "step_up_auth"
    ANALYST_INFO = "analyst_info"


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class TriggerInfo:
    """Parsed trigger from case_pack.csv."""
    trigger_type: TriggerType
    trigger_text: str
    flagged_txn_id: str
    card_id: str
    customer_id: str
    risk_score: Optional[float] = None
    opened_at: str = ""


@dataclass
class EvidenceItem:
    """A single piece of evidence for the answer file."""
    claim: str
    source: str            # "graph" | "document" | "customer" | "external"
    ref: str               # query name, doc section, or request id
    entity_ids: list[str] = field(default_factory=list)


@dataclass
class EvidenceRequest:
    """An evidence request the agent made during investigation."""
    type: str              # "customer_validation" | "step_up_auth" | "analyst_info"
    asked_after_step: int
    assumed_response: str


@dataclass
class ActionRecommendation:
    """A single recommended action with approval route and policy citation."""
    action: str            # ActionType value
    route: str             # ApprovalRoute value
    reason: str            # Policy rule citation


@dataclass
class NextBestActions:
    """Pre- and post-evidence action recommendations."""
    initial: list[ActionRecommendation] = field(default_factory=list)
    final: list[ActionRecommendation] = field(default_factory=list)
    what_changed: str = "nothing"


@dataclass
class SARReport:
    """Suspicious Activity Report (Part 2 of the answer)."""
    file: bool = False
    reason: str = ""
    narrative: str = ""
    subjects: list[str] = field(default_factory=list)
    total_amount_usd: float = 0.0
    activity_dates: list[str] = field(default_factory=list)


@dataclass
class DecisionLogEntry:
    """Append-only entry in the decisions_log."""
    timestamp: str
    state: str
    reasoning: str


@dataclass
class PatternMatch:
    """Result from a fraud pattern detection query."""
    pattern: FraudPattern
    matched: bool
    confidence: float
    evidence_txn_ids: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


# ── Main case state ────────────────────────────────────────────────────────────

@dataclass
class CaseState:
    """
    The complete state carried through every node of the LangGraph state
    machine. Directly serializable to the answer JSON format.
    """
    # Identity
    case_id: str = ""
    trigger: Optional[TriggerInfo] = None

    # Investigation results
    entities_examined: list[str] = field(default_factory=list)
    transactions_examined: list[str] = field(default_factory=list)
    patterns_matched: list[PatternMatch] = field(default_factory=list)
    similar_prior_cases: list[str] = field(default_factory=list)

    # Evidence
    evidence: list[EvidenceItem] = field(default_factory=list)
    evidence_requests: list[EvidenceRequest] = field(default_factory=list)

    # Assessment
    status: CaseStatus = CaseStatus.OPEN
    verdict: Verdict = Verdict.UNCERTAIN
    fraud_probability: float = 0.5
    pattern: FraudPattern = FraudPattern.NONE
    pattern_description: str = ""
    affected_txn_ids: list[str] = field(default_factory=list)
    first_suspicious_txn_id: str = ""
    connected_card_ids: list[str] = field(default_factory=list)
    connected_device_profiles: list[str] = field(default_factory=list)
    exposure_usd: float = 0.0

    # Case record
    summary: str = ""
    written_to_graph: bool = False
    graph_case_id: str = ""

    # Actions
    next_best_actions: NextBestActions = field(default_factory=NextBestActions)

    # SAR
    sar: SARReport = field(default_factory=SARReport)

    # Meta
    decisions_log: list[DecisionLogEntry] = field(default_factory=list)
    stop_reason: str = ""
    tool_calls: int = 0
    tokens: int = 0
    latency_s: float = 0.0
    _start_time: float = field(default_factory=time.time)

    # Internal state for the orchestrator
    current_step: int = 0
    evidence_loop_count: int = 0
    sufficient_evidence: bool = False

    # ── Graph context (not serialized to answer) ───────────────────────────
    _graph_context: dict[str, Any] = field(default_factory=dict)
    _customer_profile: dict[str, Any] = field(default_factory=dict)
    _card_history: list[dict] = field(default_factory=list)
    _device_info: dict[str, Any] = field(default_factory=dict)
    _closed_cases: list[dict] = field(default_factory=list)

    def log_decision(self, state: str, reasoning: str) -> None:
        """Append an entry to the decisions log."""
        from datetime import datetime, timezone
        self.decisions_log.append(DecisionLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            state=state,
            reasoning=reasoning,
        ))
        self.current_step += 1

    def elapsed(self) -> float:
        """Wall-clock seconds since case processing started."""
        return time.time() - self._start_time

    def to_answer_dict(self) -> dict[str, Any]:
        """Serialize to the exact answer JSON format from the README."""
        return {
            "case_id": self.case_id,
            "case": {
                "status": self.status.value,
                "verdict": self.verdict.value,
                "fraud_probability": round(self.fraud_probability, 2),
                "pattern": self.pattern.value,
                "pattern_description": self.pattern_description,
                "affected_txn_ids": self.affected_txn_ids,
                "first_suspicious_txn_id": self.first_suspicious_txn_id,
                "connected_card_ids": self.connected_card_ids,
                "connected_device_profiles": self.connected_device_profiles,
                "exposure_usd": round(self.exposure_usd, 2),
                "evidence": [
                    {
                        "claim": e.claim,
                        "source": e.source,
                        "ref": e.ref,
                        "entity_ids": e.entity_ids,
                    }
                    for e in self.evidence
                ],
                "similar_prior_cases": self.similar_prior_cases,
                "summary": self.summary,
                "written_to_graph": self.written_to_graph,
                "graph_case_id": self.graph_case_id,
            },
            "evidence_requests": [
                {
                    "type": er.type,
                    "asked_after_step": er.asked_after_step,
                    "assumed_response": er.assumed_response,
                }
                for er in self.evidence_requests
            ],
            "next_best_actions": {
                "initial": [
                    {"action": a.action, "route": a.route, "reason": a.reason}
                    for a in self.next_best_actions.initial
                ],
                "final": [
                    {"action": a.action, "route": a.route, "reason": a.reason}
                    for a in self.next_best_actions.final
                ],
                "what_changed": self.next_best_actions.what_changed,
            },
            "sar": {
                "file": self.sar.file,
                "reason": self.sar.reason,
                "narrative": self.sar.narrative,
                "subjects": self.sar.subjects,
                "total_amount_usd": round(self.sar.total_amount_usd, 2),
                "activity_dates": self.sar.activity_dates,
            },
            "stop_reason": self.stop_reason,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "latency_s": round(self.elapsed(), 1),
        }
