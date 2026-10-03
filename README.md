# data-pipeline-doctor

A zero-dependency **static health checker** for data engineering projects.
It reads your Airflow DAGs, dbt models, SQL and Python files, flags common
problems, and gives the project a score out of 100. **It never imports or
executes your code.**

```
$ data-pipeline-doctor ./my-pipeline

ERROR    DBT001  models/schema.yml:12  model 'orders' has no not_null or unique test
ERROR    SEC001  dags/load_users.py:8  'api_key' is assigned a hardcoded string literal
WARNING  AIR001  dags/load_users.py:21  DAG(...) has no retries (pass retries= or default_args=)
WARNING  SQL001  sql/daily_revenue.sql:3  SELECT * found; list the columns explicitly

Category scores:
  orchestration     96/100
  transformations   96/100
  security          90/100
  testing           90/100

Overall score: 72/100  (2 error(s), 2 warning(s))
```

## Features

- **Zero runtime dependencies.** Only the Python standard library (`ast`, `re`, `json`, `pathlib`, `argparse`).
- **Static only.** Files are read as text or parsed with `ast`. Nothing runs.
- **Scored.** Start at 100. Each **error** costs 10 points and each **warning** costs 4, in the overall score and in each category.
- **Easy to extend.** A rule is one decorated function.
- **CI-friendly.** Exit code `1` on errors (or below `--fail-under`), and `--format json` for machine-readable output.

## Installation

Requires Python 3.9 or newer.

```bash
git clone https://github.com/mayuriphad/data-pipeline-doctor.git
cd data-pipeline-doctor
pip install .
```

For development, install in editable mode so changes take effect immediately:

```bash
pip install -e .
```

You can also run it without installing:

```bash
python cli.py ./my-pipeline
```

## Usage

```bash
data-pipeline-doctor [PATH]                    # scan a directory (default: .)
data-pipeline-doctor PATH --format json        # JSON output for tooling
data-pipeline-doctor PATH --rules SQL001 SEC001
data-pipeline-doctor PATH --fail-under 80      # CI gate on the overall score
data-pipeline-doctor --list-rules
```

## Rules

| ID      | Category        | Severity | What it checks |
|---------|-----------------|----------|----------------|
| AIR001  | orchestration   | warning  | `BaseOperator` or `DAG` instantiated without `retries` (for `DAG`, `default_args=` also counts) |
| SQL001  | transformations | warning  | `SELECT *` (case-insensitive) instead of explicit columns |
| DBT001  | testing         | error    | A dbt model in `schema.yml` / `models.yml` with neither a `not_null` nor a `unique` test |
| SEC001  | security        | error    | A variable named like `password`, `api_key` or `secret` assigned a raw string literal |

### Limitations

- **AIR001** checks `BaseOperator` and `DAG` constructors directly. Subclasses such as `BashOperator` are not inspected yet.
- **DBT001** uses a small line-based YAML reader, because the standard library has no YAML parser. It handles the standard dbt `models:` layout. It does not yet read `sources:`.
- **SEC001** looks at assignments only. It does not inspect keyword arguments or dict literals, and it may flag a non-secret variable whose name contains `secret`.
- **SQL001** ignores matches on lines that contain a `--` comment before the match. It does not parse SQL.

## Adding a rule

Rules live in `checks.py`, or in any module that is imported before `run()`. A rule
is a function that takes `(path, text)` and yields `(line, message)` pairs:

```python
from pathlib import Path
from engine import WARNING, check

@check(id="PD001", category="transformations", severity=WARNING,
       files="*.py", description="pandas read_csv without dtype")
def pandas_read_csv_dtype(path: Path, text: str):
    """pd.read_csv called without an explicit dtype."""
    for no, line in enumerate(text.splitlines(), 1):
        if "read_csv(" in line and "dtype=" not in line:
            yield no, "read_csv() without dtype=; types will be inferred"
```

Rule IDs must be unique. Categories must be one of `orchestration`,
`transformations`, `security`, `testing`. Severity must be `error` or `warning`.

## Scoring

```
overall  = max(0, 100 - sum(penalties across all findings))
category = max(0, 100 - sum(penalties within that category))
penalty  = 10 per error, 4 per warning
```

## License

MIT
