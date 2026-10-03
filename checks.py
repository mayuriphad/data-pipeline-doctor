"""Built-in rules.

Every rule is a function decorated with ``@check``. To add one, write a
function that takes ``(path, text)`` and yields ``(line, message)`` pairs,
then register it. Rules are only ever read as text or parsed with ``ast``;
they are never imported or run.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterator, Optional

from engine import ERROR, WARNING, check


def _parse(text: str) -> Optional[ast.AST]:
    """Parse Python source, returning None if it is not valid Python."""
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _call_name(node: ast.Call) -> Optional[str]:
    """Return ``DAG`` for both ``DAG(...)`` and ``airflow.DAG(...)``."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


# --------------------------------------------------------------------------
# AIR001: Airflow retries
# --------------------------------------------------------------------------

#: For each constructor, the keyword arguments that count as declaring retries.
#: Airflow reads DAG retries from ``default_args`` as well as the ``retries`` kwarg.
_RETRY_KEYWORDS = {
    "BaseOperator": ("retries",),
    "DAG": ("retries", "default_args"),
}


@check(id="AIR001", category="orchestration", severity=WARNING,
       description="BaseOperator or DAG instantiated without retries")
def airflow_retries(path: Path, text: str) -> Iterator[tuple[int, str]]:
    """BaseOperator or DAG instantiated without retries."""
    tree = _parse(text)
    if tree is None:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name not in _RETRY_KEYWORDS:
            continue
        given = {kw.arg for kw in node.keywords}
        if not given.intersection(_RETRY_KEYWORDS[name]):
            yield node.lineno, f"{name}(...) has no retries (pass retries= or default_args=)"


# --------------------------------------------------------------------------
# SQL001: SELECT *
# --------------------------------------------------------------------------

_SELECT_STAR_RE = re.compile(r"\bSELECT\s+(?:[A-Za-z_][\w$]*\.)?\*", re.IGNORECASE)


@check(id="SQL001", category="transformations", severity=WARNING, files="*.sql",
       description="SELECT * instead of explicit column names")
def sql_select_star(path: Path, text: str) -> Iterator[tuple[int, str]]:
    """SELECT * used instead of explicit column names."""
    for match in _SELECT_STAR_RE.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if "--" in text[line_start:match.start()]:  # commented out
            continue
        line = text.count("\n", 0, match.start()) + 1
        yield line, "SELECT * found; list the columns explicitly"


# --------------------------------------------------------------------------
# DBT001: dbt models without not_null / unique tests
# --------------------------------------------------------------------------

_MODEL_RE = re.compile(r"^(\s*)-\s*name:\s*['\"]?([\w.\-]+)['\"]?\s*$")
_TEST_RE = re.compile(r"^\s*-?\s*['\"]?(not_null|unique)['\"]?\s*:?\s*$")


def _dbt_models(text: str) -> Iterator[tuple[int, str, list[str]]]:
    """Yield ``(line_no, model_name, block_lines)`` for each model in a dbt YAML file.

    This is a deliberately small line-based reader, since the standard library
    has no YAML parser. It handles the standard dbt layout::

        models:
          - name: orders
            columns:
              - name: order_id
                tests:
                  - not_null
    """
    models: list[tuple[int, str, list[str]]] = []
    in_models = False
    model_indent: Optional[int] = None

    for no, line in enumerate(text.splitlines(), 1):
        if line.strip() and not line[0].isspace() and not line.startswith("#"):
            # A non-indented key starts or ends the `models:` section.
            in_models = line.rstrip() == "models:"
            model_indent = None
            continue
        if not in_models:
            continue
        match = _MODEL_RE.match(line)
        # Only the first `- name:` indent counts as a model; deeper ones are columns.
        if match and (model_indent is None or len(match.group(1)) == model_indent):
            model_indent = len(match.group(1))
            models.append((no, match.group(2), []))
        elif models:
            models[-1][2].append(line)

    yield from models


@check(id="DBT001", category="testing", severity=ERROR,
       files=("*schema.yml", "*models.yml", "*schema.yaml", "*models.yaml"),
       description="dbt model has no not_null or unique test")
def dbt_missing_tests(path: Path, text: str) -> Iterator[tuple[int, str]]:
    """dbt model with neither a not_null nor a unique test."""
    for line_no, name, block in _dbt_models(text):
        if not any(_TEST_RE.match(entry) for entry in block):
            yield line_no, f"model '{name}' has no not_null or unique test"


# --------------------------------------------------------------------------
# SEC001: hardcoded secrets
# --------------------------------------------------------------------------

_SECRET_NAME_RE = re.compile(r"password|api_?key|secret", re.IGNORECASE)


def _target_name(target: ast.expr) -> Optional[str]:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


@check(id="SEC001", category="security", severity=ERROR,
       description="secret-like variable assigned a raw string literal")
def hardcoded_secrets(path: Path, text: str) -> Iterator[tuple[int, str]]:
    """Secret-like variable assigned a raw string literal."""
    tree = _parse(text)
    if tree is None:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value):
            continue
        for target in targets:
            name = _target_name(target)
            if name and _SECRET_NAME_RE.search(name):
                yield node.lineno, f"'{name}' is assigned a hardcoded string literal"
