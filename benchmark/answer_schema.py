"""
Answer file JSON schema validator.

Validates that generated answer files match the exact format specified
in the README Answer Format section. Rejects any file with missing
required fields, wrong types, invalid enum values, or logical
inconsistencies.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


# Valid enum values (from README)
VALID_STATUSES = {"open", "closed_fraud", "closed_legitimate", "escalated"}
VALID_VERDICTS = {"fraud", "legitimate", "uncertain"}
VALID_PATTERNS = {
    "card_testing", "card_not_present_fraud", "card_not_present_new_device",
    "out_of_region_use", "account_takeover", "undocumented", "none",
}
VALID_ACTIONS = {
    "ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD",
    "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER",
    "STEP_UP_AUTH", "BLOCK_CARD", "BLOCK_ALL_CARDS", "GENERATE_REPORT",
    "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD",
}
VALID_ROUTES = {"auto", "L1", "L2"}
VALID_EVIDENCE_REQUEST_TYPES = {"customer_validation", "step_up_auth", "analyst_info"}
VALID_EVIDENCE_SOURCES = {"graph", "document", "customer", "external"}


class ValidationError:
    """A single validation issue."""
    def __init__(self, field: str, message: str, severity: str = "error"):
        self.field = field
        self.message = message
        self.severity = severity  # "error" or "warning"

    def __str__(self):
        return f"[{self.severity.upper()}] {self.field}: {self.message}"


def validate_answer(data: dict[str, Any]) -> list[ValidationError]:
    """
    Validate an answer dict against the README schema.
    Returns a list of ValidationError objects (empty = valid).
    """
    errors: list[ValidationError] = []

    # ── Top level ──────────────────────────────────────────────────────────
    if not isinstance(data.get("case_id"), str) or not data["case_id"]:
        errors.append(ValidationError("case_id", "Missing or empty"))

    for field, expected_type in [
        ("stop_reason", str),
        ("tool_calls", int),
        ("tokens", int),
        ("latency_s", (int, float)),
    ]:
        if field not in data:
            errors.append(ValidationError(field, "Missing"))
        elif not isinstance(data[field], expected_type):
            errors.append(ValidationError(field, f"Expected {expected_type.__name__}, got {type(data[field]).__name__}"))

    # ── Part 1: case ───────────────────────────────────────────────────────
    case = data.get("case")
    if not isinstance(case, dict):
        errors.append(ValidationError("case", "Missing or not an object"))
        return errors  # Can't validate further

    # Status
    if case.get("status") not in VALID_STATUSES:
        errors.append(ValidationError("case.status", f"Invalid: '{case.get('status')}'. Must be one of {VALID_STATUSES}"))

    # Verdict
    if case.get("verdict") not in VALID_VERDICTS:
        errors.append(ValidationError("case.verdict", f"Invalid: '{case.get('verdict')}'. Must be one of {VALID_VERDICTS}"))

    # Fraud probability
    fp = case.get("fraud_probability")
    if not isinstance(fp, (int, float)) or fp < 0 or fp > 1:
        errors.append(ValidationError("case.fraud_probability", f"Must be number 0-1, got {fp}"))

    # Pattern
    if case.get("pattern") not in VALID_PATTERNS:
        errors.append(ValidationError("case.pattern", f"Invalid: '{case.get('pattern')}'. Must be one of {VALID_PATTERNS}"))

    # Pattern description required when undocumented
    if case.get("pattern") == "undocumented" and not case.get("pattern_description"):
        errors.append(ValidationError("case.pattern_description", "Required when pattern is 'undocumented'"))

    # Affected transactions
    if not isinstance(case.get("affected_txn_ids"), list):
        errors.append(ValidationError("case.affected_txn_ids", "Must be a list"))

    # Legitimate verdict consistency
    if case.get("verdict") == "legitimate":
        if case.get("affected_txn_ids"):
            errors.append(ValidationError("case.affected_txn_ids", "Should be empty for legitimate verdict"))
        if case.get("exposure_usd", 0) > 0:
            errors.append(ValidationError("case.exposure_usd", "Should be 0 for legitimate verdict"))

    # Exposure
    if not isinstance(case.get("exposure_usd"), (int, float)):
        errors.append(ValidationError("case.exposure_usd", "Must be a number"))

    # Evidence
    evidence = case.get("evidence")
    if not isinstance(evidence, list):
        errors.append(ValidationError("case.evidence", "Must be a list"))
    else:
        for i, e in enumerate(evidence):
            if not isinstance(e, dict):
                errors.append(ValidationError(f"case.evidence[{i}]", "Must be an object"))
                continue
            if not isinstance(e.get("claim"), str) or not e["claim"]:
                errors.append(ValidationError(f"case.evidence[{i}].claim", "Missing or empty"))
            if e.get("source") not in VALID_EVIDENCE_SOURCES:
                errors.append(ValidationError(f"case.evidence[{i}].source", f"Invalid source: '{e.get('source')}'"))

    # Summary
    if not isinstance(case.get("summary"), str) or not case["summary"]:
        errors.append(ValidationError("case.summary", "Missing or empty"))

    # Written to graph
    if not isinstance(case.get("written_to_graph"), bool):
        errors.append(ValidationError("case.written_to_graph", "Must be boolean"))

    # ── Evidence requests ──────────────────────────────────────────────────
    er_list = data.get("evidence_requests")
    if not isinstance(er_list, list):
        errors.append(ValidationError("evidence_requests", "Must be a list"))
    else:
        for i, er in enumerate(er_list):
            if not isinstance(er, dict):
                errors.append(ValidationError(f"evidence_requests[{i}]", "Must be an object"))
                continue
            if er.get("type") not in VALID_EVIDENCE_REQUEST_TYPES:
                errors.append(ValidationError(f"evidence_requests[{i}].type", f"Invalid type: '{er.get('type')}'"))
            if not isinstance(er.get("asked_after_step"), int):
                errors.append(ValidationError(f"evidence_requests[{i}].asked_after_step", "Must be int"))
            if not isinstance(er.get("assumed_response"), str):
                errors.append(ValidationError(f"evidence_requests[{i}].assumed_response", "Must be string"))

    # ── Part 3: next_best_actions ──────────────────────────────────────────
    nba = data.get("next_best_actions")
    if not isinstance(nba, dict):
        errors.append(ValidationError("next_best_actions", "Missing or not an object"))
    else:
        for phase in ["initial", "final"]:
            actions = nba.get(phase)
            if not isinstance(actions, list):
                errors.append(ValidationError(f"next_best_actions.{phase}", "Must be a list"))
                continue
            if not actions:
                errors.append(ValidationError(f"next_best_actions.{phase}", "Must have at least one action"))
            for i, a in enumerate(actions):
                if not isinstance(a, dict):
                    errors.append(ValidationError(f"next_best_actions.{phase}[{i}]", "Must be an object"))
                    continue
                if a.get("action") not in VALID_ACTIONS:
                    errors.append(ValidationError(f"next_best_actions.{phase}[{i}].action", f"Invalid: '{a.get('action')}'"))
                if a.get("route") not in VALID_ROUTES:
                    errors.append(ValidationError(f"next_best_actions.{phase}[{i}].route", f"Invalid: '{a.get('route')}'"))
                if not isinstance(a.get("reason"), str) or not a["reason"]:
                    errors.append(ValidationError(f"next_best_actions.{phase}[{i}].reason", "Missing or empty"))

        if not isinstance(nba.get("what_changed"), str):
            errors.append(ValidationError("next_best_actions.what_changed", "Must be a string"))

    # ── Part 2: sar ────────────────────────────────────────────────────────
    sar = data.get("sar")
    if not isinstance(sar, dict):
        errors.append(ValidationError("sar", "Missing or not an object"))
    else:
        if not isinstance(sar.get("file"), bool):
            errors.append(ValidationError("sar.file", "Must be boolean"))

        if not isinstance(sar.get("reason"), str):
            errors.append(ValidationError("sar.reason", "Must be a string"))

        # SAR/FILE_REPORT consistency
        final_actions = []
        if isinstance(nba, dict) and isinstance(nba.get("final"), list):
            final_actions = [a.get("action") for a in nba["final"] if isinstance(a, dict)]

        has_file_report = "FILE_REPORT" in final_actions
        sar_file = sar.get("file", False)

        if sar_file != has_file_report:
            errors.append(ValidationError(
                "sar.file",
                f"SAR file={sar_file} but FILE_REPORT {'is' if has_file_report else 'is not'} in final actions"
            ))

        if sar_file:
            if not isinstance(sar.get("narrative"), str) or not sar["narrative"]:
                errors.append(ValidationError("sar.narrative", "Required when file is true"))
            if not isinstance(sar.get("subjects"), list) or not sar["subjects"]:
                errors.append(ValidationError("sar.subjects", "Required when file is true"))
            if not isinstance(sar.get("total_amount_usd"), (int, float)):
                errors.append(ValidationError("sar.total_amount_usd", "Must be a number when file is true"))
            if not isinstance(sar.get("activity_dates"), list):
                errors.append(ValidationError("sar.activity_dates", "Must be a list when file is true"))
        else:
            # When no SAR, narrative should be empty
            if sar.get("narrative"):
                errors.append(ValidationError("sar.narrative", "Should be empty when file is false", "warning"))

    return errors


def validate_file(filepath: str | Path) -> tuple[bool, list[ValidationError]]:
    """
    Validate an answer JSON file.
    Returns (is_valid, errors).
    """
    filepath = Path(filepath)

    if not filepath.exists():
        return False, [ValidationError("file", f"File not found: {filepath}")]

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, [ValidationError("file", f"Invalid JSON: {e}")]

    errors = validate_answer(data)
    is_valid = not any(e.severity == "error" for e in errors)

    return is_valid, errors


def validate_directory(dirpath: str | Path) -> dict[str, tuple[bool, list[ValidationError]]]:
    """
    Validate all answer JSON files in a directory.
    Returns a dict of {filename: (is_valid, errors)}.
    """
    dirpath = Path(dirpath)
    results = {}

    for filepath in sorted(dirpath.glob("*.json")):
        is_valid, errors = validate_file(filepath)
        results[filepath.name] = (is_valid, errors)

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python answer_schema.py <file_or_directory>")
        sys.exit(1)

    target = Path(sys.argv[1])

    if target.is_dir():
        results = validate_directory(target)
        total = len(results)
        valid = sum(1 for v, _ in results.values() if v)

        print(f"\n{'='*60}")
        print(f"Validation Results: {valid}/{total} valid")
        print(f"{'='*60}")

        for name, (is_valid, errors) in results.items():
            status = "v VALID" if is_valid else "x INVALID"
            print(f"\n{status} — {name}")
            for e in errors:
                print(f"  {e}")

        sys.exit(0 if valid == total else 1)
    else:
        is_valid, errors = validate_file(target)
        status = "v VALID" if is_valid else "x INVALID"
        print(f"{status} — {target.name}")
        for e in errors:
            print(f"  {e}")
        sys.exit(0 if is_valid else 1)
