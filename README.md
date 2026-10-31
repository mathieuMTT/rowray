# RowRay

RowRay compares one dbt model from a Git base ref with the current working tree, including committed, staged, unstaged, and untracked changes. It targets dbt projects using BigQuery and never materializes the model.

## What it compares

By default, RowRay compiles both versions and uses BigQuery dry runs to report:

- Bytes scanned and estimated on-demand cost
- Top-level columns added or removed
- Top-level column type changes

With `--execute`, RowRay runs guarded aggregate queries and also reports:

- Row counts
- Null rates
- Approximate distinct counts
- Sums for numeric columns

With `--column NAME`, it adds BASE and working-tree row counts plus percentage variation for every value in one column.

## Requirements

- Python 3.10+
- Git
- An existing dbt project connected to BigQuery
- A `dbt` executable available in the current shell

Before installing RowRay, this must succeed from the dbt project:

```bash
dbt debug
```

RowRay reuses the project's `dbt_project.yml`, `profiles.yml`, target, environment variables, and BigQuery credentials. OAuth/ADC, `service-account`, and `service-account-json` profiles are supported.

## Install

Install RowRay from PyPI with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install rowray
rowray --help
```

If uv says its executable directory is missing from `PATH`, run `uv tool update-shell` and restart the shell.

Run once without permanent installation:

```bash
uvx rowray --help
```

## Use

Run RowRay inside the existing dbt project:

```bash
rowray diff --select my_model
```

The Git base defaults to `origin/HEAD`, then `origin/main`, then `origin/master`. Override it or the dbt target when needed:

```bash
rowray diff --select my_model --base origin/main --target dev
```

Compile against a production target while submitting RowRay's BigQuery jobs from a project where you have `bigquery.jobs.create`:

```bash
rowray diff --select my_model --target prod --execution-project my-dev-project
```

`--execution-project` overrides `execution_project` from the selected profile output, which otherwise falls back to its `project`. It affects RowRay dry runs and aggregate queries; dbt compilation still uses `--target`.

Pass required dbt variables as YAML or JSON. Identical values are used for BASE and the working tree:

```bash
rowray diff \
  --select my_model \
  --vars '{"execution_date":"2026-10-29","is_backfill":false}'
```

Forward dbt warning handling when the project promotes warnings to errors:

```bash
rowray diff \
  --select my_model \
  --warn-error-options '{"silence":["CustomKeyInConfigDeprecation"]}'
```

Add full-refresh scan estimates for BASE and the working tree. This runs two extra compiles and dry runs, but still does not materialize the model:

```bash
rowray diff --select my_model --full-refresh
```

By default, full-refresh compiles reuse `--vars`. Override only the values that differ by merging `--full-refresh-vars` over them:

```bash
rowray diff \
  --select my_model \
  --vars '{"execution_date":"2026-09-29","inception_date":"2026-09-23","is_backfill":false}' \
  --full-refresh \
  --full-refresh-vars '{"inception_date":"2019-10-23","is_backfill":true}'
```

## Compare incremental data

Comparing rows and column metrics requires explicit execution:

```bash
rowray diff --select my_model --execute
```

Analyze counts and variation for every value of one shared scalar column:

```bash
rowray diff --select my_model --execute --column page_type
```

`--column` prints actual distinct values. Do not use it on sensitive or very high-cardinality columns unless that terminal output is acceptable.

## Execution and cost safety

- BASE compiles in an isolated Git worktree.
- The current dbt project compiles concurrently with all local changes and an isolated target directory.
- BigQuery comparisons are dry-run only unless `--execute` is passed.
- `--execution-project` changes where RowRay submits BigQuery jobs, not which datasets the compiled SQL reads.
- `--full-refresh` runs incremental and full-refresh compiles together; full-refresh SQL is dry-run only.
- `--execute` and `--column` compare only the incremental SQL, never the full-refresh SQL.
- Each aggregate query is dry-run first and skipped above 200 GiB.
- Each executed aggregate query also sets `maximum_bytes_billed` to 200 GiB.
- `--column` runs an additional aggregate query with its own 200 GiB cap.
- Raw rows are not downloaded, but `--column` downloads distinct values and their counts.
- dbt compilation may run warehouse introspection or project macros such as `run_query`; RowRay cannot make arbitrary dbt projects query-free.

The displayed USD amount is an estimate:

```text
bytes processed / 1024⁴ × $6.25
```

`$6.25/TiB` is a fixed [BigQuery on-demand list price](https://cloud.google.com/bigquery/pricing), not fetched automatically. The estimate does not account for the free tier, capacity pricing, reservations, discounts, currency, or prior monthly usage.

## Current limits

- One selected model per command
- BigQuery only
- Top-level schema comparison only
- Schema comparison checks column names and types, not `NULLABLE`/`REQUIRED` mode changes
- Data comparison skips repeated, nested, `GEOGRAPHY`, and `JSON` columns
- Distinct counts use `APPROX_COUNT_DISTINCT`
- `--column` returns every value without an output limit
- The current project must already have its dbt dependencies installed; RowRay installs dependencies only in the BASE worktree

## Update or remove

```bash
uv tool upgrade rowray
uv tool uninstall rowray
```

## License

Copyright 2026 Mathieu Rozé.

Licensed under the [Apache License 2.0](LICENSE).
