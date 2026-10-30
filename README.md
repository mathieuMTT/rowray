# RowRay

RowRay shows how a dbt code change affects data before merge, without requiring a development build.

The first version is a local, open-source CLI for dbt and BigQuery.

## Try the first feature

RowRay expects an existing Git repository where `dbt compile` already works against BigQuery.

```bash
cd your-dbt-project
uvx --from /path/to/rowray rowray diff --select my_model
```

RowRay compiles the selected model at the Git base ref and `HEAD`, then uses BigQuery dry runs to compare bytes scanned and estimated on-demand cost. No model is built and no query is executed.

The base defaults to `origin/HEAD`, `origin/main`, or `origin/master`. Override dbt or Git selection when needed:

```bash
rowray diff --select my_model --base origin/main --target dev
```

Requirements: Python 3.10+, Git, and the `dbt` command used by the project. RowRay reads the existing dbt profile and supports BigQuery OAuth/ADC plus `service-account` profiles.

See [ROADMAP.md](ROADMAP.md) for the project stages.
