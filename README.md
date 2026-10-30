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

RowRay compiles the selected model at the Git base ref and `HEAD`, then uses BigQuery dry runs to compare bytes scanned and estimated on-demand cost. No model is built and no query is executed.

The base defaults to `origin/HEAD`, `origin/main`, or `origin/master`. Override dbt or Git selection when needed:

```bash
rowray diff --select my_model --base origin/main --target dev
```

OAuth/ADC and `service-account` profiles are supported. Only committed changes in `HEAD` are compared.

## Update or remove

```bash
uv tool upgrade rowray
uv tool uninstall rowray
```

See [ROADMAP.md](ROADMAP.md) for the project stages.
