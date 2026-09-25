"""
Benchmark runner — run all 20 cases and produce answer files.

Usage:
    python -m benchmark.run_all                 # Run all 20 cases
    python -m benchmark.run_all HHG-001         # Run a single case
    python -m benchmark.run_all --validate      # Validate existing answers
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.progress import track

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.orchestrator import FraudInvestigationAgent
from agent.tools.tigergraph_tools import DataLayer
from agent import config
from benchmark.answer_schema import validate_file, validate_directory

console = Console()
logger = logging.getLogger(__name__)


def run_all_cases(case_ids: list[str] | None = None) -> dict[str, Any]:
    """
    Run investigation on all (or selected) cases.

    Returns a summary dict with per-case results.
    """
    data = DataLayer()
    agent = FraudInvestigationAgent(data)
    case_pack = data.case_pack

    if case_ids:
        case_pack = case_pack[case_pack["case_id"].isin(case_ids)]
        if case_pack.empty:
            console.print(f"[red]No cases found for IDs: {case_ids}[/red]")
            return {}

    total = len(case_pack)
    console.print(f"\n[bold cyan]Running {total} case(s)...[/bold cyan]\n")

    results = {}
    start_time = time.time()

    for _, row in case_pack.iterrows():
        case_id = row["case_id"]
        console.print(f"[yellow]> {case_id}[/yellow] — {row.get('trigger_type', '?')}: {row.get('trigger_text', '')[:60]}...")

        try:
            row_dict = row.to_dict()
            state = agent.investigate_case(row_dict)
            path = agent.save_answer(state)

            # Validate immediately
            is_valid, errors = validate_file(path)
            error_count = sum(1 for e in errors if e.severity == "error")
            warning_count = sum(1 for e in errors if e.severity == "warning")

            results[case_id] = {
                "path": str(path),
                "verdict": state.verdict.value,
                "fraud_probability": state.fraud_probability,
                "pattern": state.pattern.value,
                "exposure_usd": state.exposure_usd,
                "actions": len(state.next_best_actions.final),
                "sar_filed": state.sar.file,
                "evidence_count": len(state.evidence),
                "evidence_requests": len(state.evidence_requests),
                "tool_calls": state.tool_calls,
                "latency_s": round(state.elapsed(), 1),
                "valid": is_valid,
                "errors": error_count,
                "warnings": warning_count,
            }

            status = "[green]v[/green]" if is_valid else f"[red]x ({error_count} errors)[/red]"
            console.print(
                f"  {status} {state.verdict.value} "
                f"(p={state.fraud_probability:.2f}), "
                f"pattern={state.pattern.value}, "
                f"${state.exposure_usd:.2f}"
            )

        except Exception as e:
            console.print(f"  [red]x FAILED: {e}[/red]")
            results[case_id] = {"error": str(e)}

    elapsed = time.time() - start_time

    # ── Summary table ──────────────────────────────────────────────────────
    console.print(f"\n{'='*60}")
    console.print(f"[bold]Results: {total} cases in {elapsed:.1f}s[/bold]\n")

    table = Table(title="Case Results")
    table.add_column("Case", style="cyan")
    table.add_column("Verdict")
    table.add_column("Prob")
    table.add_column("Pattern")
    table.add_column("Exposure")
    table.add_column("SAR")
    table.add_column("Evidence")
    table.add_column("Valid")

    for case_id, r in results.items():
        if "error" in r:
            table.add_row(case_id, "[red]ERROR[/red]", "", "", "", "", "", r["error"][:30])
            continue

        verdict_style = {
            "fraud": "red",
            "legitimate": "green",
            "uncertain": "yellow",
        }.get(r["verdict"], "white")

        table.add_row(
            case_id,
            f"[{verdict_style}]{r['verdict']}[/{verdict_style}]",
            f"{r['fraud_probability']:.2f}",
            r["pattern"],
            f"${r['exposure_usd']:.2f}",
            "Yes" if r["sar_filed"] else "No",
            str(r["evidence_count"]),
            "[green]v[/green]" if r["valid"] else f"[red]x{r['errors']}[/red]",
        )

    console.print(table)

    # Stats
    valid_count = sum(1 for r in results.values() if r.get("valid"))
    console.print(f"\n[bold]Schema-valid: {valid_count}/{total}[/bold]")

    # Save summary
    summary_path = config.CASES_OUTPUT_DIR / "_summary.json"
    with open(summary_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    console.print(f"Summary saved to {summary_path}")

    return results


def main():
    parser = argparse.ArgumentParser(description="HHGoa Fraud Agent Benchmark Runner")
    parser.add_argument("cases", nargs="*", help="Case IDs to run (default: all 20)")
    parser.add_argument("--validate", action="store_true", help="Only validate existing answer files")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.validate:
        console.print("\n[bold]Validating existing answer files...[/bold]\n")
        results = validate_directory(config.CASES_OUTPUT_DIR)
        total = len(results)
        valid = sum(1 for v, _ in results.values() if v)

        for name, (is_valid, errors) in results.items():
            status = "[green]v[/green]" if is_valid else "[red]x[/red]"
            console.print(f"  {status} {name}")
            for e in errors:
                console.print(f"    {e}")

        console.print(f"\n[bold]{valid}/{total} valid[/bold]")
        sys.exit(0 if valid == total else 1)

    case_ids = args.cases if args.cases else None
    results = run_all_cases(case_ids)

    # Exit code based on validity
    all_valid = all(r.get("valid", False) for r in results.values())
    sys.exit(0 if all_valid else 1)


if __name__ == "__main__":
    main()
