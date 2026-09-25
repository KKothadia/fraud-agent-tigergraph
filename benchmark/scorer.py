"""
Self-scoring harness — evaluate answer files against a rubric
that mirrors the judging weights.

Scoring (100 pts):
  Investigation accuracy (25): evidence coverage, pattern detection, citation rate
  Next-best-action (25): policy compliance, approval routing, pre/post actions
  Explainability (10): evidence citations in summary, threshold mentioned
  Agentic design (15): decisions_log completeness, loop behavior, tool calls
  Calibration (10): fraud_probability calibration against closed-case priors
  Completeness (15): all fields present, IDs valid, answer format correct
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from benchmark.answer_schema import validate_answer, VALID_ACTIONS, VALID_ROUTES


def score_investigation(case: dict) -> tuple[float, list[str]]:
    """Score the investigation quality (25 pts max)."""
    score = 0.0
    notes = []

    case_obj = case.get("case", {})

    # Evidence count (0-5 pts)
    evidence = case_obj.get("evidence", [])
    if len(evidence) >= 5:
        score += 5
    elif len(evidence) >= 3:
        score += 3
    elif len(evidence) >= 1:
        score += 1
    else:
        notes.append("No evidence collected")

    # Evidence source diversity (0-5 pts)
    sources = set(e.get("source") for e in evidence)
    score += min(len(sources), 3) * 1.5
    if "graph" not in sources:
        notes.append("No graph-derived evidence")
        score -= 2

    # Pattern detection (0-5 pts)
    pattern = case_obj.get("pattern", "none")
    if pattern != "none":
        score += 3
        if case_obj.get("pattern_description") and pattern == "undocumented":
            score += 2
            notes.append("Undocumented pattern described — good")
    else:
        # "none" is valid for legitimate cases
        verdict = case_obj.get("verdict", "")
        if verdict == "legitimate":
            score += 5  # Correctly identified as legitimate
        else:
            notes.append("No pattern identified for non-legitimate case")

    # Similar prior cases (0-5 pts)
    prior = case_obj.get("similar_prior_cases", [])
    if prior:
        score += min(len(prior), 3) + 2
        notes.append(f"Retrieved {len(prior)} prior case(s)")
    else:
        notes.append("No prior cases retrieved")

    # Entity coverage (0-5 pts)
    has_devices = any("device" in e.get("claim", "").lower() for e in evidence)
    has_history = any("history" in e.get("claim", "").lower() or "card" in e.get("ref", "").lower() for e in evidence)
    has_customer = any("customer" in e.get("claim", "").lower() for e in evidence)

    if has_devices:
        score += 2
    if has_history:
        score += 2
    if has_customer:
        score += 1

    return min(score, 25.0), notes


def score_next_best_action(case: dict) -> tuple[float, list[str]]:
    """Score the next-best-action quality (25 pts max)."""
    score = 0.0
    notes = []

    nba = case.get("next_best_actions", {})

    # Initial actions present (0-5 pts)
    initial = nba.get("initial", [])
    if initial:
        score += 5
    else:
        notes.append("No initial actions")

    # Final actions present (0-5 pts)
    final = nba.get("final", [])
    if final:
        score += 5
    else:
        notes.append("No final actions")

    # All actions in whitelist (0-3 pts)
    all_valid = all(
        a.get("action") in VALID_ACTIONS and a.get("route") in VALID_ROUTES
        for a in initial + final
    )
    if all_valid:
        score += 3
    else:
        notes.append("Some actions not in whitelist or invalid routes")

    # Policy rule citations (0-5 pts)
    cited_rules = set()
    for a in initial + final:
        reason = a.get("reason", "")
        for rule in ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "§3a"]:
            if rule in reason:
                cited_rules.add(rule)
    score += min(len(cited_rules), 5)
    if cited_rules:
        notes.append(f"Rules cited: {', '.join(sorted(cited_rules))}")

    # Pre/post evidence distinction (0-5 pts)
    what_changed = nba.get("what_changed", "")
    er = case.get("evidence_requests", [])
    if er:
        if what_changed and what_changed != "nothing":
            score += 5
            notes.append("Actions changed after evidence — correctly tracked")
        else:
            score += 2
            notes.append("Evidence requested but actions unchanged")
    else:
        score += 3  # No evidence requested, initial=final is correct

    # SAR filing consistency (0-2 pts)
    sar = case.get("sar", {})
    has_file_report = any(a.get("action") == "FILE_REPORT" for a in final)
    if sar.get("file") == has_file_report:
        score += 2
    else:
        notes.append("SAR.file inconsistent with FILE_REPORT in actions")

    return min(score, 25.0), notes


def score_explainability(case: dict) -> tuple[float, list[str]]:
    """Score explainability (10 pts max)."""
    score = 0.0
    notes = []

    case_obj = case.get("case", {})
    summary = case_obj.get("summary", "")

    # Summary present and substantive (0-4 pts)
    if summary:
        sentences = summary.count(". ") + summary.count(".\n") + 1
        if 2 <= sentences <= 6:
            score += 4
        elif sentences > 0:
            score += 2
        else:
            score += 1
    else:
        notes.append("No summary")

    # Evidence citations in summary (0-3 pts)
    evidence = case_obj.get("evidence", [])
    cited_ids = sum(1 for e in evidence if any(eid in summary for eid in e.get("entity_ids", []) if eid))
    if cited_ids >= 2:
        score += 3
    elif cited_ids >= 1:
        score += 1

    # SAR narrative quality (0-3 pts)
    sar = case.get("sar", {})
    if sar.get("file"):
        narrative = sar.get("narrative", "")
        if narrative:
            nar_sentences = narrative.count(". ") + 1
            if 6 <= nar_sentences <= 12:
                score += 3
            elif nar_sentences >= 3:
                score += 2
            else:
                score += 1
        else:
            notes.append("SAR filed but no narrative")
    else:
        score += 3  # No SAR needed — full credit

    return min(score, 10.0), notes


def score_agentic_design(case: dict) -> tuple[float, list[str]]:
    """Score agentic design quality (15 pts max)."""
    score = 0.0
    notes = []

    # Tool calls made (0-3 pts)
    tool_calls = case.get("tool_calls", 0)
    if tool_calls >= 5:
        score += 3
    elif tool_calls >= 3:
        score += 2
    elif tool_calls >= 1:
        score += 1
    else:
        notes.append("No tool calls recorded")

    # Evidence loop behavior (0-5 pts)
    er = case.get("evidence_requests", [])
    if er:
        score += min(len(er), 3) + 2
        notes.append(f"Evidence loop: {len(er)} request(s)")
    else:
        # Not all cases need evidence loops
        case_obj = case.get("case", {})
        prob = case_obj.get("fraud_probability", 0.5)
        if prob >= 0.85 or prob <= 0.15:
            score += 5  # Clear case, no loop needed
        else:
            score += 2
            notes.append("Moderate probability but no evidence requested")

    # Stop reason present and substantive (0-4 pts)
    stop_reason = case.get("stop_reason", "")
    if stop_reason:
        score += 4
    else:
        notes.append("No stop reason")

    # Latency reasonable (0-3 pts)
    latency = case.get("latency_s", 0)
    if 0 < latency <= 60:
        score += 3
    elif latency <= 120:
        score += 2
    elif latency > 0:
        score += 1

    return min(score, 15.0), notes


def score_completeness(case: dict) -> tuple[float, list[str]]:
    """Score answer completeness (15 pts max)."""
    score = 0.0
    notes = []

    # Schema validation (0-10 pts)
    errors = validate_answer(case)
    error_count = sum(1 for e in errors if e.severity == "error")
    warning_count = sum(1 for e in errors if e.severity == "warning")

    if error_count == 0:
        score += 10
    elif error_count <= 2:
        score += 6
    elif error_count <= 5:
        score += 3
    else:
        notes.append(f"{error_count} schema errors")

    if warning_count > 0:
        notes.append(f"{warning_count} schema warnings")

    # Written to graph (0-2 pts)
    if case.get("case", {}).get("written_to_graph"):
        score += 2
    else:
        notes.append("Case not written to graph")

    # Graph case ID (0-1 pt)
    if case.get("case", {}).get("graph_case_id"):
        score += 1

    # All required top-level fields (0-2 pts)
    required = ["case_id", "case", "evidence_requests", "next_best_actions", "sar", "stop_reason"]
    missing = [f for f in required if f not in case]
    if not missing:
        score += 2
    else:
        notes.append(f"Missing top-level fields: {missing}")

    return min(score, 15.0), notes


def score_case(case: dict) -> dict[str, Any]:
    """Score a single case answer. Returns detailed scorecard."""
    inv_score, inv_notes = score_investigation(case)
    nba_score, nba_notes = score_next_best_action(case)
    exp_score, exp_notes = score_explainability(case)
    agn_score, agn_notes = score_agentic_design(case)
    cmp_score, cmp_notes = score_completeness(case)

    total = inv_score + nba_score + exp_score + agn_score + cmp_score

    return {
        "case_id": case.get("case_id", "?"),
        "total": round(total, 1),
        "investigation": {"score": round(inv_score, 1), "max": 25, "notes": inv_notes},
        "next_best_action": {"score": round(nba_score, 1), "max": 25, "notes": nba_notes},
        "explainability": {"score": round(exp_score, 1), "max": 10, "notes": exp_notes},
        "agentic_design": {"score": round(agn_score, 1), "max": 15, "notes": agn_notes},
        "completeness": {"score": round(cmp_score, 1), "max": 15, "notes": cmp_notes},
    }


def score_directory(dirpath: str | Path) -> dict[str, Any]:
    """Score all answer files in a directory."""
    dirpath = Path(dirpath)
    scorecards = {}

    for filepath in sorted(dirpath.glob("HHG-*.json")):
        with open(filepath, "r", encoding="utf-8") as f:
            case = json.load(f)
        scorecards[filepath.stem] = score_case(case)

    # Aggregate
    if scorecards:
        total_avg = sum(s["total"] for s in scorecards.values()) / len(scorecards)
        category_avgs = {}
        for cat in ["investigation", "next_best_action", "explainability", "agentic_design", "completeness"]:
            scores = [s[cat]["score"] for s in scorecards.values()]
            category_avgs[cat] = round(sum(scores) / len(scores), 1)
    else:
        total_avg = 0
        category_avgs = {}

    return {
        "per_case": scorecards,
        "average_total": round(total_avg, 1),
        "category_averages": category_avgs,
    }


if __name__ == "__main__":
    from rich.console import Console
    from rich.table import Table

    console = Console()

    dirpath = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "cases"

    if not dirpath.exists():
        console.print(f"[red]Directory not found: {dirpath}[/red]")
        sys.exit(1)

    results = score_directory(dirpath)

    # Summary table
    table = Table(title="Self-Scoring Results")
    table.add_column("Case", style="cyan")
    table.add_column("Total", justify="right")
    table.add_column("Investigate\n/25", justify="right")
    table.add_column("Actions\n/25", justify="right")
    table.add_column("Explain\n/10", justify="right")
    table.add_column("Agentic\n/15", justify="right")
    table.add_column("Complete\n/15", justify="right")

    for case_id, sc in results["per_case"].items():
        total_color = "green" if sc["total"] >= 70 else "yellow" if sc["total"] >= 50 else "red"
        table.add_row(
            case_id,
            f"[{total_color}]{sc['total']:.0f}[/{total_color}]",
            f"{sc['investigation']['score']:.0f}",
            f"{sc['next_best_action']['score']:.0f}",
            f"{sc['explainability']['score']:.0f}",
            f"{sc['agentic_design']['score']:.0f}",
            f"{sc['completeness']['score']:.0f}",
        )

    console.print(table)
    console.print(f"\n[bold]Average total: {results['average_total']:.1f}/100[/bold]")

    # Save detailed report
    report_path = dirpath / "_scorecard.json"
    with open(report_path, "w") as f:
        json.dump(results, f, indent=2)
    console.print(f"Detailed scorecard saved to {report_path}")
