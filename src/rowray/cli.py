import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from google.cloud import bigquery
from google.oauth2 import service_account

PRICE_PER_TIB = 6.25
PROFILE_SCAN_LIMIT = 200 * 1024**3
NUMERIC_TYPES = {"INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC", "DECIMAL", "BIGDECIMAL"}
SKIPPED_TYPES = {"RECORD", "STRUCT", "GEOGRAPHY", "JSON"}
ENV_VAR = re.compile(r'''^\s*\{\{\s*env_var\(["']([^"']+)["'](?:,\s*["']([^"']*)["'])?\)\s*\}\}\s*$''')


class RowRayError(Exception):
    pass


def run(command: list[str], cwd: Path) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        detail = "\n".join(output.strip() for output in (result.stdout, result.stderr) if output.strip())
        raise RowRayError(f"{' '.join(command)} failed\n{detail}")
    return result.stdout.strip()


def find_project(start: Path) -> Path:
    for path in (start, *start.parents):
        if (path / "dbt_project.yml").is_file():
            return path
    raise RowRayError("dbt_project.yml not found; run RowRay inside a dbt project")


def find_profiles(project: Path, specified: Path | None) -> Path:
    candidates = [
        specified,
        Path(os.environ["DBT_ENGINE_PROFILES_DIR"]) if "DBT_ENGINE_PROFILES_DIR" in os.environ else None,
        Path(os.environ["DBT_PROFILES_DIR"]) if "DBT_PROFILES_DIR" in os.environ else None,
        project,
        Path("~/.dbt").expanduser(),
    ]
    for path in candidates:
        if path and (path.expanduser() / "profiles.yml").is_file():
            return path.expanduser().resolve()
    raise RowRayError("profiles.yml not found; pass --profiles-dir PATH")


def resolve_env(value: object) -> object:
    if not isinstance(value, str):
        return value
    match = ENV_VAR.match(value)
    if not match:
        return os.path.expandvars(value)
    name, default = match.groups()
    if name in os.environ:
        return os.environ[name]
    if default is not None:
        return default
    raise RowRayError(f"environment variable {name!r} is required by the dbt profile")


def default_base(repo: Path) -> str:
    try:
        return run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo)
    except RowRayError:
        for ref in ("origin/main", "origin/master"):
            try:
                run(["git", "rev-parse", "--verify", ref], repo)
                return ref
            except RowRayError:
                pass
    raise RowRayError("base ref not found; pass --base REF")


def compiled_sql(target_path: Path, model: str) -> str:
    manifest_path = target_path / "manifest.json"
    if not manifest_path.is_file():
        raise RowRayError(f"dbt did not create {manifest_path}")
    nodes = json.loads(manifest_path.read_text())["nodes"].values()
    matches = [
        node
        for node in nodes
        if node.get("resource_type") == "model"
        and model in (node.get("name"), node.get("alias"), node.get("unique_id"))
    ]
    if len(matches) != 1:
        raise RowRayError(f"expected one model named {model!r}, found {len(matches)}")
    sql = matches[0].get("compiled_code") or matches[0].get("compiled_sql")
    if not sql:
        raise RowRayError(f"model {model!r} has no compiled SQL")
    return sql.strip().removesuffix(";").strip()


def compile_model(
    dbt: str,
    project: Path,
    target_path: Path,
    model: str,
    profiles_dir: Path,
    target: str | None,
    dbt_vars: str | None,
    warn_error_options: str | None,
    full_refresh: bool,
) -> str:
    common = ["--project-dir", str(project), "--profiles-dir", str(profiles_dir)]
    if target:
        common += ["--target", target]
    if (project / "packages.yml").is_file() or (project / "dependencies.yml").is_file():
        run([dbt, "deps", *common], project)
    compile_args = [dbt, "compile"]
    if full_refresh:
        compile_args.append("--full-refresh")
    compile_args += [*common, "--target-path", str(target_path), "--select", model]
    if dbt_vars:
        compile_args += ["--vars", dbt_vars]
    if warn_error_options:
        compile_args += ["--warn-error-options", warn_error_options]
    run(compile_args, project)
    return compiled_sql(target_path, model)


def profile_output(project: Path, profiles_dir: Path, target: str | None) -> dict:
    project_config = yaml.safe_load((project / "dbt_project.yml").read_text())
    if not isinstance(project_config, dict):
        raise RowRayError("dbt_project.yml must contain a mapping")
    profile_name = resolve_env(project_config.get("profile") or project_config.get("name"))
    profiles_path = profiles_dir / "profiles.yml"
    if not profiles_path.is_file():
        raise RowRayError(f"profiles.yml not found in {profiles_dir}")
    profiles = yaml.safe_load(profiles_path.read_text())
    if not isinstance(profiles, dict):
        raise RowRayError("profiles.yml must contain a mapping")
    profile = profiles.get(profile_name)
    if not profile:
        raise RowRayError(f"profile {profile_name!r} not found in {profiles_path}")
    target_name = target or profile.get("target")
    output = profile.get("outputs", {}).get(target_name)
    if not output or output.get("type") != "bigquery":
        raise RowRayError(f"target {target_name!r} is not a BigQuery output")
    return output


def bigquery_client(output: dict) -> bigquery.Client:
    project = resolve_env(output.get("execution_project") or output.get("project")) or None
    method = output.get("method")
    if method == "service-account":
        keyfile = resolve_env(output.get("keyfile"))
        if not keyfile:
            raise RowRayError("BigQuery service-account profile has no keyfile")
        credentials = service_account.Credentials.from_service_account_file(str(Path(str(keyfile)).expanduser()))
        return bigquery.Client(project=project, credentials=credentials)
    if method == "service-account-json":
        credentials = service_account.Credentials.from_service_account_info(output.get("keyfile_json", {}))
        return bigquery.Client(project=project, credentials=credentials)
    if method == "oauth":
        return bigquery.Client(project=project)
    raise RowRayError(f"BigQuery authentication method {method!r} is not supported yet")


def dry_run(client: bigquery.Client, sql: str) -> tuple[int, list[tuple[str, str, str]]]:
    config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    job = client.query(sql, job_config=config)
    columns = [(field.name, field.field_type, field.mode or "NULLABLE") for field in (job.schema or [])]
    return job.total_bytes_processed or 0, columns


def schema_changes(
    base: list[tuple[str, str]], head: list[tuple[str, str]]
) -> tuple[list[str], list[str], list[str]]:
    base_types = dict(base)
    head_types = dict(head)
    added = [name for name in head_types if name not in base_types]
    removed = [name for name in base_types if name not in head_types]
    changed = [
        f"{name} {base_types[name]} → {head_types[name]}"
        for name in head_types
        if name in base_types and head_types[name] != base_types[name]
    ]
    return added, removed, changed


def profile_columns(
    base: list[tuple[str, str, str]], head: list[tuple[str, str, str]]
) -> tuple[list[tuple[str, str]], list[str]]:
    head_by_name = {name: (field_type, mode) for name, field_type, mode in head}
    shared = [
        (name, field_type, head_by_name[name][1])
        for name, field_type, _mode in base
        if name in head_by_name and head_by_name[name][0] == field_type
    ]
    kept = [(name, field_type) for name, field_type, mode in shared if mode != "REPEATED" and field_type not in SKIPPED_TYPES]
    skipped = [name for name, field_type, mode in shared if mode == "REPEATED" or field_type in SKIPPED_TYPES]
    return kept, skipped


def identifier(name: str) -> str:
    return f"`{name.replace('`', '')}`"


def profile_sql(base_sql: str, head_sql: str, columns: list[tuple[str, str]]) -> str:
    metrics = ["COUNT(*) AS n_rows"]
    for name, field_type in columns:
        column = identifier(name)
        metrics.append(f"COUNTIF({column} IS NULL) AS {identifier(name + '__nulls')}")
        metrics.append(f"APPROX_COUNT_DISTINCT({column}) AS {identifier(name + '__distincts')}")
        if field_type in NUMERIC_TYPES:
            metrics.append(f"SUM({column}) AS {identifier(name + '__sum')}")
    body = ",\n    ".join(metrics)
    return (
        "WITH base AS (\n"
        f"{base_sql}\n"
        "), head AS (\n"
        f"{head_sql}\n"
        ")\n"
        f"SELECT 'base' AS side, {body} FROM base\n"
        "UNION ALL\n"
        f"SELECT 'head' AS side, {body} FROM head"
    )


def percentage(part: float, total: float) -> str:
    return "0%" if not total else f"{100 * part / total:.1f}%"


def delta(old: float, new: float) -> str:
    if old == 0:
        return "0" if new == 0 else "new"
    return f"{(new - old) / old:+.1%}"


def column_moved(base: dict, head: dict, name: str, field_type: str) -> bool:
    if percentage(base[name + "__nulls"], base["n_rows"]) != percentage(head[name + "__nulls"], head["n_rows"]):
        return True
    if int(base[name + "__distincts"]) != int(head[name + "__distincts"]):
        return True
    return field_type in NUMERIC_TYPES and base[name + "__sum"] != head[name + "__sum"]


def format_bytes(value: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.2f} TiB"


def format_delta(base: int, head: int) -> str:
    if not base:
        return "0.0%" if not head else "n/a"
    return f"{(head - base) / base:+.1%}"


def report(
    base: int,
    head: int,
    base_columns: list[tuple[str, str, str]],
    head_columns: list[tuple[str, str, str]],
    columns: list[tuple[str, str]],
    skipped: list[str],
    profile: dict[str, dict] | None,
    profile_note: str,
) -> str:
    base_cost = base / 1024**4 * PRICE_PER_TIB
    head_cost = head / 1024**4 * PRICE_PER_TIB
    lines = [
        f"{'':12}{'BASE':>14}{'HEAD':>14}{'DELTA':>14}",
        f"{'Scan':12}{format_bytes(base):>14}{format_bytes(head):>14}{format_delta(base, head):>14}",
        f"{'Cost (USD)':12}{f'${base_cost:.4f}':>14}{f'${head_cost:.4f}':>14}{f'${head_cost - base_cost:+.4f}':>14}",
        "",
    ]
    added, removed, changed = schema_changes(
        [(name, field_type) for name, field_type, _mode in base_columns],
        [(name, field_type) for name, field_type, _mode in head_columns],
    )
    lines.extend(
        [
            f"added: {', '.join(added) or 'none'}",
            f"removed: {', '.join(removed) or 'none'}",
            f"type changes: {', '.join(changed) or 'none'}",
        ]
    )
    if skipped:
        lines.append(f"skipped columns: {', '.join(skipped)}")
    lines.append("")
    if profile is None:
        lines.append(profile_note)
    else:
        base_row = profile["base"]
        head_row = profile["head"]
        if base_row["n_rows"] == 0 and head_row["n_rows"] == 0:
            lines.append("rows: 0; no data to profile")
            columns = []
        elif base_row["n_rows"] == head_row["n_rows"]:
            lines.append("rows: no change")
        else:
            lines.append(f"rows: {base_row['n_rows']} → {head_row['n_rows']} ({delta(base_row['n_rows'], head_row['n_rows'])})")
        moved = [(name, field_type) for name, field_type in columns if column_moved(base_row, head_row, name, field_type)]
        unchanged = [name for name, field_type in columns if not column_moved(base_row, head_row, name, field_type)]
        if moved:
            lines.extend(["", "changes", "", "| column | nulls | distincts | sum |", "| --- | --- | --- | --- |"]) 
            for name, field_type in moved:
                nulls = (
                    f"{percentage(base_row[name + '__nulls'], base_row['n_rows'])} → "
                    f"{percentage(head_row[name + '__nulls'], head_row['n_rows'])}"
                )
                distincts = f"{int(base_row[name + '__distincts'])} → {int(head_row[name + '__distincts'])}"
                total = f"{base_row[name + '__sum']} → {head_row[name + '__sum']}" if field_type in NUMERIC_TYPES else ""
                lines.append(f"| {name} | {nulls} | {distincts} | {total} |")
        if unchanged:
            lines.extend(["", "no change", "", "| column | distincts |", "| --- | --- |"]) 
            lines.extend(f"| {name} | {int(head_row[name + '__distincts'])} |" for name in unchanged)
    lines.append(f"\nOn-demand estimate at ${PRICE_PER_TIB:.2f}/TiB; no model is built.")
    return "\n".join(lines)


def compare(
    model: str,
    base: str | None,
    profiles_dir: Path | None,
    target: str | None,
    dbt_vars: str | None,
    warn_error_options: str | None,
    full_refresh: bool,
    execute: bool,
) -> str:
    dbt = shutil.which("dbt")
    if not dbt:
        raise RowRayError("dbt not found; activate the environment used by this project")
    project = find_project(Path.cwd().resolve())
    profiles_dir = find_profiles(project, profiles_dir)
    repo = Path(run(["git", "rev-parse", "--show-toplevel"], project))
    try:
        relative_project = project.relative_to(repo)
    except ValueError as error:
        raise RowRayError("dbt project is not inside its Git repository") from error
    base = base or default_base(repo)

    worktrees: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix="rowray-") as temporary:
            temporary_path = Path(temporary)
            sql = {}
            for label, ref in (("base", base), ("head", "HEAD")):
                worktree = temporary_path / label
                run(["git", "worktree", "add", "--detach", str(worktree), ref], repo)
                worktrees.append(worktree)
                sql[label] = compile_model(
                    dbt,
                    worktree / relative_project,
                    temporary_path / f"{label}-target",
                    model,
                    profiles_dir,
                    target,
                    dbt_vars,
                    warn_error_options,
                    full_refresh,
                )
            client = bigquery_client(profile_output(project, profiles_dir, target))
            base_bytes, base_columns = dry_run(client, sql["base"])
            head_bytes, head_columns = dry_run(client, sql["head"])
            columns, skipped = profile_columns(base_columns, head_columns)
            profile = None
            profile_note = "Profile not run; pass --execute to compare rows, nulls, distincts, and numeric sums."
            if execute:
                query = profile_sql(sql["base"], sql["head"], columns)
                profile_bytes, _schema = dry_run(client, query)
                if profile_bytes > PROFILE_SCAN_LIMIT:
                    profile_note = f"Profile skipped: {format_bytes(profile_bytes)} exceeds {format_bytes(PROFILE_SCAN_LIMIT)} limit."
                else:
                    config = bigquery.QueryJobConfig(maximum_bytes_billed=PROFILE_SCAN_LIMIT)
                    profile = {row["side"]: dict(row.items()) for row in client.query(query, job_config=config).result()}
                    if set(profile) != {"base", "head"}:
                        raise RowRayError("BigQuery profile did not return both base and head rows")
            return report(base_bytes, head_bytes, base_columns, head_columns, columns, skipped, profile, profile_note)
    finally:
        for worktree in reversed(worktrees):
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo,
                capture_output=True,
            )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="rowray", description="Compare dbt model scan costs between Git refs")
    subcommands = result.add_subparsers(dest="command", required=True)
    diff = subcommands.add_parser("diff")
    diff.add_argument("--select", required=True, metavar="MODEL")
    diff.add_argument("--base", metavar="REF")
    diff.add_argument("--profiles-dir", type=Path)
    diff.add_argument("--target")
    diff.add_argument("--vars", dest="dbt_vars", metavar="YAML")
    diff.add_argument("--warn-error-options", metavar="YAML")
    diff.add_argument("--full-refresh", action="store_true")
    diff.add_argument("--execute", action="store_true", help="execute aggregate data profiles after a guarded dry run")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        print(
            compare(
                args.select,
                args.base,
                args.profiles_dir,
                args.target,
                args.dbt_vars,
                args.warn_error_options,
                args.full_refresh,
                args.execute,
            )
        )
        return 0
    except (OSError, RowRayError, yaml.YAMLError, ValueError) as error:
        print(f"rowray: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
