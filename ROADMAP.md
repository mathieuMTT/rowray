# RowRay roadmap

## Product goal

Given a dbt project and a Git base ref, RowRay compiles both versions, estimates query cost, and reports how the resulting data changes.

The first release is intentionally narrow:

- Local CLI only
- dbt projects
- BigQuery
- Aggregate comparisons, not raw row downloads
- Terminal, JSON, and Markdown output

## Stage 1: technical spike

Goal: prove the core workflow with one model and BigQuery.

- Create the Python project with `uv`
- Add a small `rowray` CLI
- Locate a dbt project and profiles directory
- Create isolated HEAD and base Git worktrees
- Run `dbt compile` for both refs in separate processes
- Read compiled SQL from versioned dbt artifacts
- Run BigQuery dry runs
- Print scan bytes and estimated cost
- Record timing for every phase

Exit criteria:

```bash
rowray diff --base origin/main --select my_model
```

compiles both sides and prints their scan estimates without executing the model.

## Stage 2: useful local MVP

Goal: provide enough evidence to review a dbt change locally.

- Detect changed SQL models when `--select` is omitted
- Compare schemas
- Compare row counts
- Profile nulls, approximate distinct counts, and numeric sums
- Add value distribution focus for one column
- Support incremental and full-refresh compilation
- Add cost limits before query execution
- Return clear non-zero exit codes on failure
- Produce one stable JSON result format
- Render terminal and Markdown reports from that JSON

Exit criteria:

- A user can understand model impact from one command
- Full refresh is dry-run only
- Expensive queries are blocked before execution
- The same result can be consumed by a human or CI system

## Stage 3: reliable open-source release

Goal: make RowRay safe for people outside the original development environment.

- Define supported Python and dbt versions
- Add unit tests for SQL generation and report rendering
- Add adapter contract tests
- Add a public example dbt project
- Test Linux and macOS in CI
- Document authentication, configuration, costs, and limitations
- Add contribution, security, and release documentation
- Build and publish signed Python wheels to PyPI
- Keep telemetry absent or strictly opt-in

Exit criteria:

- A new user can install and run RowRay from the README
- CI builds from a clean checkout
- First tagged release is available on PyPI

## Stage 4: beta and product validation

Goal: confirm that teams use the tool repeatedly before building a hosted product.

- Recruit 5 to 10 external dbt users
- Observe onboarding without guiding every step
- Track time to first successful diff
- Record unsupported dbt patterns and warehouse failures
- Learn which report sections affect merge decisions
- Interview users about CI automation and willingness to pay

Exit criteria:

- At least three teams use RowRay weekly
- Users run it on real pull requests
- One repeated paid problem is clearly identified

## Stage 5: second warehouse

Goal: validate the adapter boundary only after BigQuery works well.

- Extract a small warehouse adapter interface
- Add the warehouse most requested by beta users
- Keep comparison semantics and JSON output consistent
- Document unavoidable differences in cost estimation and SQL behavior

Snowflake is a likely candidate, but demand decides the second adapter.

Exit criteria:

- The second adapter passes the same contract tests
- Core logic contains no warehouse-specific branching

## Stage 6: paid automation

Goal: sell workflow and collaboration around the open-source engine.

Potential paid features:

- GitHub and GitLab applications
- Automatic pull-request validation
- Hosted reports and history
- Team policies and cost budgets
- Merge checks
- Shared configuration across repositories
- RBAC, SSO, and audit logs
- Private networking or bring-your-own-cloud deployment
- Support and service-level agreements

Do not start this stage until the beta shows repeated demand for automation.

## Performance rules

- Push aggregation to the warehouse
- Never download raw tables by default
- Dry-run before execution when the warehouse supports it
- Cache dbt artifacts by Git SHA, dbt version, target, and variables
- Compile independent refs in separate processes
- Add concurrency only around measured waiting time
- Port code to Rust only when profiling finds a local CPU bottleneck

## Explicit non-goals for version 0.1

- Web application
- Hosted control plane
- Every warehouse
- Full row-level diff for unlimited tables
- AI-generated explanations
- Custom SQL parser
- Rust extension
- Production data observability

## Open decisions

- Final license
- Supported dbt versions
- Configuration file name and schema
- Exact handling of incremental windows
- How users define primary keys for future row-level diffs
- Whether CI integration belongs in the CLI repository or a separate repository
