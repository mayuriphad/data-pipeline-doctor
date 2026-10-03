"""Command-line interface: ``data-pipeline-doctor [PATH]``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import checks  # noqa: F401  (importing registers the built-in rules)
from engine import ERROR, REGISTRY, WARNING, Report, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="data-pipeline-doctor",
        description="Static health checker for Airflow, dbt, Pandas and SQL projects. "
                    "Reads your files; never executes them.",
    )
    parser.add_argument("path", nargs="?", default=".",
                        help="project directory to scan (default: current directory)")
    parser.add_argument("--format", choices=("text", "json"), default="text",
                        help="output format (default: text)")
    parser.add_argument("--rules", nargs="+", metavar="ID",
                        help="only run these rule IDs, e.g. --rules SQL001 SEC001")
    parser.add_argument("--list-rules", action="store_true",
                        help="list available rules and exit")
    parser.add_argument("--fail-under", type=int, metavar="SCORE",
                        help="exit 1 if the overall score is below SCORE "
                             "(default: exit 1 when any error is found)")
    return parser


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def render_text(report: Report) -> str:
    lines = [f"data-pipeline-doctor: {report.root}",
             f"Scanned {report.files_scanned} file(s)", ""]

    if not report.findings:
        lines.append("No issues found.")
    for f in report.findings:
        lines.append(f"{f.severity.upper():<8} {f.rule_id:<7} "
                     f"{_rel(f.path, report.root)}:{f.line}  {f.message}")

    errors = sum(1 for f in report.findings if f.severity == ERROR)
    warnings = sum(1 for f in report.findings if f.severity == WARNING)

    lines += ["", "Category scores:"]
    for category, score in report.category_scores().items():
        lines.append(f"  {category:<16} {score:>3}/100")

    lines += ["", f"Overall score: {report.score}/100  "
                  f"({errors} error(s), {warnings} warning(s))"]

    for problem in report.errors:
        lines.append(f"skipped: {problem}")
    return "\n".join(lines)


def to_dict(report: Report) -> dict:
    return {
        "root": str(report.root),
        "score": report.score,
        "categories": report.category_scores(),
        "files_scanned": report.files_scanned,
        "findings": [
            {
                "rule": f.rule_id,
                "category": f.category,
                "severity": f.severity,
                "file": _rel(f.path, report.root),
                "line": f.line,
                "message": f.message,
            }
            for f in report.findings
        ],
        "errors": report.errors,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        for rule in sorted(REGISTRY.values(), key=lambda r: r.id):
            print(f"{rule.id:<8} {rule.category:<16} {rule.severity:<8} {rule.description}")
        return 0

    if args.rules:
        unknown = [r for r in args.rules if r not in REGISTRY]
        if unknown:
            parser.error(f"unknown rule id(s): {', '.join(unknown)}")

    try:
        report = run(args.path, args.rules)
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(to_dict(report), indent=2))
    else:
        print(render_text(report))

    if args.fail_under is not None:
        failed = report.score < args.fail_under
    else:
        failed = any(f.severity == ERROR for f in report.findings)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
