# Project Instructions (AI Agents)

These instructions are for coding agents working on RowRay (Codex CLI, Claude Code, etc.).

Paths in this file are relative to the repository root containing this file.

## Hard Rules

### Scope and code style

- Keep changes minimal and focused on the requested behavior.
- Prefer simple, readable functions and the standard library over new dependencies or abstractions.
- Preserve Python 3.10 compatibility.
- Follow the existing split: argument wiring in `src/rowray/cli.py`, external side effects in `src/rowray/compare.py`, and pure calculations/rendering in `src/rowray/analysis.py`.
- Do not add framework layers, plugin systems, warehouse adapters, configuration objects, or one-file-per-feature structures without a concrete current need.
- Keep examples public-safe. Never copy private model names, project IDs, datasets, credentials, recovery codes, or company-specific values into tracked files.

### Warehouse and cost safety

- RowRay runs `dbt compile`, never `dbt run`. Never add model materialization, `MERGE`, replace, drop, or write behavior.
- BigQuery comparisons remain dry-run only unless the user explicitly passes `--execute`.
- `--execute` is the boundary for the potentially billed incremental data comparison.
- `--column` requires `--execute`, runs a separate guarded query, and may expose actual distinct values in terminal output.
- `--full-refresh` adds incremental and full-refresh estimates for BASE and WORKTREE. Never execute the full-refresh SQL.
- Every executed comparison must be dry-run first, skipped above 200 GiB, and use `maximum_bytes_billed=200 GiB`.
- BigQuery job project precedence is `--execution-project`, profile `execution_project`, then profile `project`. This must not change the dbt target or referenced datasets.
- Cost output is a fixed on-demand estimate at `$6.25/TiB`. Never describe it as live pricing, exact billing, or an invoice.
- dbt compilation may execute project-defined introspection or `run_query` macros. Never claim RowRay can prevent or estimate arbitrary project macro activity.

### Git and dbt behavior

- BASE is an isolated Git worktree. WORKTREE is the current dbt project, including committed, staged, unstaged, and untracked files.
- Preserve the comparison between BASE and WORKTREE; do not silently compare two committed refs instead.
- Incremental and full-refresh compiles run concurrently with separate target directories and independent progress lines.
- If BASE contains `packages.yml` or `dependencies.yml`, install its dependencies before parallel compilation. Do not reuse WORKTREE dependencies because versions may differ.
- Pass common dbt options to BASE and WORKTREE equally unless a flag explicitly documents an override.
- `--full-refresh-vars` is a shallow top-level override of `--vars` for full-refresh compiles only.

### CLI and terminal output

- Preserve existing flags and output semantics unless the user explicitly requests a change.
- Keep `rowray --help`, `rowray diff --help`, and `README.md` aligned for every user-facing change.
- User and Google API failures must end as concise `rowray:` messages, not Python tracebacks.
- Interactive progress may animate, but non-TTY/CI output must remain stable and readable.
- Concurrent jobs must use one coordinated renderer; do not let worker threads print competing spinners.

### Tests

- New parsing, branching, concurrency, money, security, or error-handling logic requires a focused test in `tests/test_cli.py`.
- Mock Git, dbt, and BigQuery in unit tests. Do not require live credentials, a warehouse, or a generated dbt project.
- Keep tests fast and use the standard library `unittest` style already present.
- Run the narrowest relevant test while iterating, then the full suite before handoff.

### Git and releases

- Do not commit, push, tag, publish, create a GitHub release, or bump the version unless the user explicitly asks.
- Treat published PyPI versions as immutable. A new release requires a new version in both `pyproject.toml` and `uv.lock`.
- Do not modify or expose ignored personal files such as recovery codes or the local roadmap.

## Running Development Commands

Run commands from the repository root.

```bash
# Full test suite
uv run --locked python -m unittest discover -s tests -v

# Public CLI contract
uv run --locked rowray --help
uv run --locked rowray diff --help

# Release build validation
uv build --no-sources
uvx twine check dist/*.whl dist/*.tar.gz
```

Do not run a live RowRay comparison against BigQuery merely to validate a code change. Live integration testing requires explicit user intent because dbt macros and `--execute` may incur warehouse activity or cost.

## Project Map

- `src/rowray/cli.py`: argparse definitions, entry point, and user-facing error formatting.
- `src/rowray/compare.py`: Git worktrees, dbt compilation, BigQuery access, concurrency, and progress output.
- `src/rowray/analysis.py`: SQL generation, schema/data comparisons, cost calculations, and terminal tables.
- `tests/test_cli.py`: unit tests and mocks.
- `README.md`: canonical user documentation, install instructions, cost behavior, and examples.
- `.github/workflows/publish.yml`: tested PyPI Trusted Publishing workflow.

## Using RowRay in a dbt Repository

When helping a user run RowRay rather than develop it:

1. Work inside their existing dbt project. Never create a dbt project or GCP connection for them.
2. Confirm `dbt debug` succeeds and the `dbt` executable is available in the active shell.
3. Start with `rowray diff --select MODEL`, which uses dry runs.
4. Forward required project values through `--vars`, `--target`, and `--warn-error-options`; do not invent project-specific defaults.
5. Use `--execution-project` when the selected data project does not grant `bigquery.jobs.create`.
6. Add `--execute` or `--column` only when the user intends to run potentially billed queries and accepts value exposure from `--column`.
7. Use `--full-refresh-vars` only for values that differ from the incremental compile.

When diagnosing a failure, preserve the exact command and error. Identify whether it came from Git, dependency installation, dbt compile, a BigQuery dry run, or an executed comparison, then fix the shared root cause rather than adding project-specific behavior.

## Where to Look Next

- Read `README.md` for the current user-facing behavior and examples.
- Read `src/rowray/cli.py` for the authoritative flag contract.
- Read `src/rowray/compare.py` for execution order, safety limits, and external interactions.
- Read `src/rowray/analysis.py` for SQL and report semantics.
- Check for a more specific nested `AGENTS.md` before modifying a subdirectory. None exist currently.

Do not create `.agents/` guidance files until this root document becomes too large or a task-specific area needs instructions that do not apply repository-wide.
