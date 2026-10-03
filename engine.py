"""Runner and scoring logic for data-pipeline-doctor.

Rules register themselves with the ``@check`` decorator (see ``checks.py``).
A rule is a function ``(path, text) -> iterable of (line, message)``. The
engine handles file discovery, severity, scoring and reporting. Nothing in
a scanned project is ever imported or executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

ERROR = "error"
WARNING = "warning"

#: Points deducted from a score per finding.
PENALTIES = {ERROR: 10, WARNING: 4}

CATEGORIES = ("orchestration", "transformations", "security", "testing")

#: Directories that are never scanned (vendored code, caches, build output).
SKIP_DIRS = {
    ".git", ".hg", ".venv", "venv", "env", "node_modules",
    "__pycache__", "dbt_packages", "target", "logs",
}

CheckFunc = Callable[[Path, str], Iterable[tuple[int, str]]]


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    severity: str
    description: str
    patterns: tuple[str, ...]
    func: CheckFunc


@dataclass(frozen=True)
class Finding:
    rule_id: str
    category: str
    severity: str
    path: Path
    line: int
    message: str


@dataclass
class Report:
    root: Path
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    errors: list[str] = field(default_factory=list)  # files a rule could not check

    @property
    def score(self) -> int:
        return score_for(self.findings)

    def category_scores(self) -> dict[str, int]:
        return {
            c: score_for(f for f in self.findings if f.category == c)
            for c in CATEGORIES
        }


REGISTRY: dict[str, Rule] = {}


def check(
    id: str,
    category: str,
    *,
    severity: str = WARNING,
    description: str = "",
    files: tuple[str, ...] | str = ("*.py",),
):
    """Register a rule. The decorated function yields ``(line, message)`` pairs.

    Example::

        @check(id="AIR001", category="orchestration", severity=WARNING)
        def airflow_retries(path, text):
            ...
            yield node.lineno, "DAG has no retries"
    """
    if category not in CATEGORIES:
        raise ValueError(f"{id}: unknown category {category!r}; expected one of {CATEGORIES}")
    if severity not in PENALTIES:
        raise ValueError(f"{id}: unknown severity {severity!r}; expected one of {tuple(PENALTIES)}")
    patterns = (files,) if isinstance(files, str) else tuple(files)

    def decorator(func: CheckFunc) -> CheckFunc:
        if id in REGISTRY:
            raise ValueError(f"duplicate rule id {id!r}")
        doc = (func.__doc__ or "").strip()
        REGISTRY[id] = Rule(
            id=id,
            category=category,
            severity=severity,
            description=description or (doc.splitlines()[0] if doc else ""),
            patterns=patterns,
            func=func,
        )
        return func

    return decorator


def score_for(findings: Iterable[Finding]) -> int:
    """Start at 100, subtract the penalty for each finding, floor at 0."""
    return max(0, 100 - sum(PENALTIES[f.severity] for f in findings))


def iter_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Return every file under ``root`` matching one of ``patterns``, skipping SKIP_DIRS."""
    found: set[Path] = set()
    for pattern in patterns:
        for path in root.rglob(pattern):
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts[:-1]
            if any(part in SKIP_DIRS for part in parts):
                continue
            found.add(path)
    return sorted(found)


def run(root: str | Path, rule_ids: Optional[Iterable[str]] = None) -> Report:
    """Run the selected rules (default: all registered rules) over ``root``."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")

    rules = [REGISTRY[r] for r in rule_ids] if rule_ids else list(REGISTRY.values())
    report = Report(root=root)
    scanned: set[Path] = set()

    for rule in rules:
        for path in iter_files(root, rule.patterns):
            scanned.add(path)
            try:
                # utf-8-sig strips the BOM that Windows editors add; ast.parse rejects it.
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                hits = list(rule.func(path, text))
            except Exception as exc:  # one broken file must not stop the whole run
                report.errors.append(f"{rule.id}: {path.relative_to(root).as_posix()}: {exc}")
                continue
            for line, message in hits:
                report.findings.append(
                    Finding(rule.id, rule.category, rule.severity, path, line, message)
                )

    report.files_scanned = len(scanned)
    report.findings.sort(key=lambda f: (str(f.path), f.line, f.rule_id))
    return report
