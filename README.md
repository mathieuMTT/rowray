# RowRay

RowRay shows how a dbt code change affects data before merge, without requiring a development build.

The first version is a local, open-source CLI for dbt and BigQuery.

## Requirements

RowRay expects Python 3.10+, Git, and an existing dbt repository connected to BigQuery. Before installing RowRay, this must succeed from the dbt project:

```bash
cd your-dbt-project
dbt debug
```

RowRay reuses that `dbt` command, `dbt_project.yml`, `profiles.yml`, target, and BigQuery credentials.

## Install

Install the current Git version with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+ssh://git@github.com/mathieuMTT/rowray.git
rowray --help
```

If uv reports that its executable directory is missing from `PATH`, run `uv tool update-shell` and restart the shell.

For one run without permanent installation:

```bash
uvx --from git+ssh://git@github.com/mathieuMTT/rowray.git rowray --help
```

## Use

Run RowRay from the existing dbt project, in the same shell where `dbt debug` works:

```bash
cd your-dbt-project
rowray diff --select my_model
```

RowRay compiles the selected model at the Git base ref and `HEAD`, then uses BigQuery dry runs to compare bytes scanned, estimated on-demand cost, and top-level columns. By default, no model is built and no data query is executed.

The base defaults to `origin/HEAD`, `origin/main`, or `origin/master`. Override dbt or Git selection when needed:

```bash
rowray diff --select my_model --base origin/main --target dev
```

Pass required dbt variables as YAML or JSON. The same values are used for both refs:

```bash
rowray diff --select my_model --vars '{"execution_date":"2026-10-29","is_backfill":false}'
```

Forward dbt warning handling when a project promotes warnings to errors:

```bash
rowray diff --select my_model --warn-error-options '{"silence":["CustomKeyInConfigDeprecation"]}'
```

Compile both refs as full refresh instead of using incremental SQL:

```bash
rowray diff --select my_model --full-refresh
```

Execute guarded aggregate profiles to compare row counts, null rates, approximate distinct counts, and numeric sums:

```bash
rowray diff --select my_model --execute
```

RowRay dry-runs the profile first and skips execution above 200 GiB. Only aggregates are downloaded; raw rows stay in BigQuery.

Each compile and BigQuery phase shows a lightweight zero-dependency spinner with elapsed time. Non-interactive logs use static progress lines.
BASE compiles in an isolated Git worktree while the current dbt project compiles concurrently with its committed and uncommitted changes. Both use isolated target directories.

OAuth/ADC and `service-account` profiles are supported. Staged, unstaged, and untracked changes inside the current dbt project are included.

## Update or remove

```bash
uv tool upgrade rowray
uv tool uninstall rowray
```

See [ROADMAP.md](ROADMAP.md) for the project stages.
