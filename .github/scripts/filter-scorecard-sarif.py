#!/usr/bin/env python3
# managed-by: repo-launch-doctor-scorecard-filter-v1
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


GOVERNANCE_ONLY_RULES = {"CIIBestPracticesID"}
SOLO_ONLY_RULES = {"CodeReviewID"}
YOUNG_REPOSITORY_MARKER = "project was created within the last 90 days"
SOLO_BRANCH_PROTECTION_MARKERS = (
    "does not require approvers",
    "codeowners review is not required",
    "'last push approval' is disabled",
)


def _message_text(result: dict[str, Any]) -> str:
    message = result.get("message")
    if not isinstance(message, dict):
        return ""
    text = message.get("text")
    return text if isinstance(text, str) else ""


def _only_solo_branch_protection_warnings(message: str) -> bool:
    warning_lines = [
        line.strip().lower()
        for line in message.splitlines()
        if line.strip().lower().startswith("warn:")
    ]
    if not warning_lines:
        return False
    return all(
        any(marker in line for marker in SOLO_BRANCH_PROTECTION_MARKERS)
        for line in warning_lines
    )


def should_suppress(result: dict[str, Any], *, solo_maintainer: bool) -> bool:
    rule_id = result.get("ruleId")
    if not isinstance(rule_id, str):
        return False
    message = _message_text(result).lower()

    if rule_id in GOVERNANCE_ONLY_RULES:
        return True
    if rule_id == "MaintainedID" and YOUNG_REPOSITORY_MARKER in message:
        return True
    if solo_maintainer and rule_id in SOLO_ONLY_RULES:
        return True
    if (
        solo_maintainer
        and rule_id == "BranchProtectionID"
        and _only_solo_branch_protection_warnings(message)
    ):
        return True
    return False


def filter_sarif(document: dict[str, Any], *, solo_maintainer: bool) -> tuple[int, int]:
    suppressed = 0
    kept = 0
    runs = document.get("runs")
    if not isinstance(runs, list):
        return suppressed, kept

    for run in runs:
        if not isinstance(run, dict):
            continue
        results = run.get("results")
        if not isinstance(results, list):
            continue
        filtered: list[Any] = []
        for item in results:
            if isinstance(item, dict) and should_suppress(item, solo_maintainer=solo_maintainer):
                suppressed += 1
                continue
            filtered.append(item)
            kept += 1
        run["results"] = filtered
    return suppressed, kept


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Keep actionable OpenSSF Scorecard findings in GitHub Code Scanning while "
            "leaving governance-only findings in the unfiltered Scorecard artifact."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--solo-maintainer", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    document = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("SARIF root must be a JSON object")
    suppressed, kept = filter_sarif(document, solo_maintainer=args.solo_maintainer)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Scorecard SARIF filter: suppressed={suppressed} kept={kept} solo_maintainer={args.solo_maintainer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
