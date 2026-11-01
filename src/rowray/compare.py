import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from contextlib import contextmanager
from pathlib import Path

import yaml
from google.cloud import bigquery
from google.oauth2 import service_account

from rowray.analysis import SKIPPED_TYPES, focus_report, focus_sql, format_bytes, profile_columns, profile_sql, report

PROFILE_SCAN_LIMIT = 200 * 1024**3
ENV_VAR = re.compile(r'''^\s*\{\{\s*env_var\(["']([^"']+)["'](?:,\s*["']([^"']*)["'])?\)\s*\}\}\s*$''')


class RowRayError(Exception):
    pass


def _color(text: str, code: int, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


@contextmanager
def progress(label: str):
    started = time.monotonic()
    animated = sys.stdout.isatty()
    stop = threading.Event()

    def spin() -> None:
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        index = 0
        while not stop.is_set():
            spinner = _color(frames[index % len(frames)], 34, True)
            print(f"\r{spinner} {label} ({time.monotonic() - started:.0f}s)", end="", flush=True)
            index += 1
            stop.wait(0.25)

    if animated:
        thread = threading.Thread(target=spin, daemon=True)
        thread.start()
    else:
        thread = None
        print(f"→ {label}...", flush=True)
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        stop.set()
        if thread:
            thread.join()
        prefix = _color("✗", 31, animated) if failed else _color("✓", 32, animated)
        clear = "\r\033[2K" if animated else ""
        print(f"{clear}{prefix} {label} ({time.monotonic() - started:.1f}s)", flush=True)


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
    install_deps: bool,
) -> str:
    common = ["--project-dir", str(project), "--profiles-dir", str(profiles_dir)]
    if target:
        common += ["--target", target]
    if install_deps and ((project / "packages.yml").is_file() or (project / "dependencies.yml").is_file()):
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


def parallel_results(futures: dict[str, Future], labels: dict[str, str]) -> dict[str, str]:
    started = time.monotonic()
    completed: dict[str, float] = {}
    animated = sys.stdout.isatty()
    future_labels = {future: label for label, future in futures.items()}

    if not animated:
        for label in futures:
            print(f"→ {labels[label]}...", flush=True)
        for future in as_completed(future_labels):
            label = future_labels[future]
            completed[label] = time.monotonic() - started
            prefix = "✗" if future.exception() else "✓"
            print(f"{prefix} {labels[label]} ({completed[label]:.1f}s)", flush=True)
    else:
        frame = 0
        rendered = False
        while True:
            if rendered:
                print(f"\033[{len(futures)}A", end="")
            lines = []
            for label, future in futures.items():
                if label in completed:
                    prefix = _color("✗", 31, True) if future.exception() else _color("✓", 32, True)
                    lines.append(f"{prefix} {labels[label]} ({completed[label]:.1f}s)")
                else:
                    spinner = _color("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[frame % 10], 34, True)
                    lines.append(f"{spinner} {labels[label]} ({time.monotonic() - started:.0f}s)")
            print("\n".join(f"\r\033[2K{line}" for line in lines), flush=True)
            rendered = True
            frame += 1
            if len(completed) == len(futures):
                break
            pending = {future for label, future in futures.items() if label not in completed}
            done, _pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                completed[future_labels[future]] = time.monotonic() - started

    return {label: future.result() for label, future in futures.items()}


def compile_refs(jobs: dict[str, tuple], labels: dict[str, str] | None = None) -> dict[str, str]:
    with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as executor:
        futures = {label: executor.submit(compile_model, *arguments) for label, arguments in jobs.items()}
        if labels:
            return parallel_results(futures, labels)
        return {label: future.result() for label, future in futures.items()}


def merge_vars(dbt_vars: str | None, overrides: str) -> str:
    base = yaml.safe_load(dbt_vars) if dbt_vars else {}
    extra = yaml.safe_load(overrides)
    if not isinstance(base, dict) or not isinstance(extra, dict):
        raise RowRayError("--vars and --full-refresh-vars must contain YAML mappings")
    return yaml.safe_dump({**base, **extra}, default_flow_style=True).strip()


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


def bigquery_client(output: dict, execution_project: str | None = None) -> bigquery.Client:
    project = execution_project or resolve_env(output.get("execution_project") or output.get("project")) or None
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


def compare(
    model: str,
    base: str | None,
    profiles_dir: Path | None,
    target: str | None,
    dbt_vars: str | None,
    warn_error_options: str | None,
    full_refresh: bool,
    execute: bool,
    column: str | None,
    full_refresh_vars: str | None = None,
    execution_project: str | None = None,
) -> str:
    if full_refresh_vars and not full_refresh:
        raise RowRayError("--full-refresh-vars requires --full-refresh")
    full_vars = merge_vars(dbt_vars, full_refresh_vars) if full_refresh_vars else dbt_vars
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
            base_worktree = temporary_path / "base"
            run(["git", "worktree", "add", "--detach", str(base_worktree), base], repo)
            worktrees.append(base_worktree)
            base_project = base_worktree / relative_project
            if (base_project / "packages.yml").is_file() or (base_project / "dependencies.yml").is_file():
                common = ["--project-dir", str(base_project), "--profiles-dir", str(profiles_dir)]
                if target:
                    common += ["--target", target]
                with progress("Install BASE dependencies"):
                    run([dbt, "deps", *common], base_project)
            jobs = {
                "base": (
                    dbt,
                    base_project,
                    temporary_path / "base-target",
                    model,
                    profiles_dir,
                    target,
                    dbt_vars,
                    warn_error_options,
                    False,
                    False,
                ),
                "head": (
                    dbt,
                    project,
                    temporary_path / "head-target",
                    model,
                    profiles_dir,
                    target,
                    dbt_vars,
                    warn_error_options,
                    False,
                    False,
                ),
            }
            if full_refresh:
                jobs.update(
                    {
                        "full_base": (
                            dbt,
                            base_project,
                            temporary_path / "full-base-target",
                            model,
                            profiles_dir,
                            target,
                            full_vars,
                            warn_error_options,
                            True,
                            False,
                        ),
                        "full_head": (
                            dbt,
                            project,
                            temporary_path / "full-head-target",
                            model,
                            profiles_dir,
                            target,
                            full_vars,
                            warn_error_options,
                            True,
                            False,
                        ),
                    }
                )
            labels = {
                "base": f"Compile incremental BASE ({base})",
                "head": "Compile incremental WORKTREE",
            }
            if full_refresh:
                labels.update(
                    {
                        "full_base": f"Compile full-refresh BASE ({base})",
                        "full_head": "Compile full-refresh WORKTREE",
                    }
                )
            sql = compile_refs(jobs, labels)
            client = bigquery_client(profile_output(project, profiles_dir, target), execution_project)
            with progress("Dry-run BASE"):
                base_bytes, base_columns = dry_run(client, sql["base"])
            with progress("Dry-run working tree"):
                head_bytes, head_columns = dry_run(client, sql["head"])
            full_bytes = None
            if full_refresh:
                with progress("Dry-run full-refresh BASE"):
                    full_base_bytes, _full_base_columns = dry_run(client, sql["full_base"])
                with progress("Dry-run full-refresh working tree"):
                    full_head_bytes, _full_head_columns = dry_run(client, sql["full_head"])
                full_bytes = (full_base_bytes, full_head_bytes)
            columns, skipped = profile_columns(base_columns, head_columns)
            profile = None
            profile_note = "Data comparison not run; pass --execute to compare rows, nulls, distincts, and numeric sums."
            focus_rows = None
            focus_note = None
            if column and not execute:
                raise RowRayError("--column requires --execute because value counts run a BigQuery query")
            if execute:
                query = profile_sql(sql["base"], sql["head"], columns)
                with progress("Dry-run incremental data comparison"):
                    profile_bytes, _schema = dry_run(client, query)
                if profile_bytes > PROFILE_SCAN_LIMIT:
                    profile_note = f"Data comparison skipped: {format_bytes(profile_bytes)} exceeds {format_bytes(PROFILE_SCAN_LIMIT)} limit."
                else:
                    with progress("Run incremental data comparison"):
                        config = bigquery.QueryJobConfig(maximum_bytes_billed=PROFILE_SCAN_LIMIT)
                        profile = {row["side"]: dict(row.items()) for row in client.query(query, job_config=config).result()}
                    if set(profile) != {"base", "head"}:
                        raise RowRayError("BigQuery data comparison did not return both base and head rows")
            if column:
                base_by_name = {name: (field_type, mode) for name, field_type, mode in base_columns}
                head_by_name = {name: (field_type, mode) for name, field_type, mode in head_columns}
                if column not in base_by_name or column not in head_by_name or base_by_name[column] != head_by_name[column]:
                    raise RowRayError(f"column {column!r} is missing or has a different type between base and working tree")
                if base_by_name[column][1] == "REPEATED" or base_by_name[column][0] in SKIPPED_TYPES:
                    raise RowRayError(f"column {column!r} is repeated or nested and cannot be focused")
                query = focus_sql(sql["base"], sql["head"], column)
                with progress(f"Dry-run column {column}"):
                    focus_bytes, _schema = dry_run(client, query)
                if focus_bytes > PROFILE_SCAN_LIMIT:
                    focus_note = f"Focus skipped: {format_bytes(focus_bytes)} exceeds {format_bytes(PROFILE_SCAN_LIMIT)} limit."
                else:
                    with progress(f"Analyze column {column}"):
                        config = bigquery.QueryJobConfig(maximum_bytes_billed=PROFILE_SCAN_LIMIT)
                        focus_rows = [dict(row.items()) for row in client.query(query, job_config=config).result()]
            output = report(base_bytes, head_bytes, base_columns, head_columns, columns, skipped, profile, profile_note, full_bytes)
            return output + (f"\n\n{focus_report(column, focus_rows, focus_note)}" if column else "")
    finally:
        for worktree in reversed(worktrees):
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo,
                capture_output=True,
            )
