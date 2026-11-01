import argparse
import re
import sys
from pathlib import Path

import yaml
from google.api_core.exceptions import GoogleAPICallError

from rowray.compare import RowRayError, compare

JOB_PERMISSION = re.compile(r"Project ([^:]+): User does not have bigquery\.jobs\.create")


def format_google_error(error: GoogleAPICallError) -> str:
    match = JOB_PERMISSION.search(str(error))
    if match:
        return f"cannot create BigQuery jobs in {match.group(1)}; pass --execution-project PROJECT"
    return f"BigQuery: {error}"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="rowray", description="X-ray dbt changes for schema drift, data shifts, and BigQuery scan cost")
    subcommands = result.add_subparsers(dest="command", required=True)
    diff = subcommands.add_parser("diff", help="compare BASE with the current working tree")
    diff.add_argument("--select", required=True, metavar="MODEL", help="one dbt model name")
    diff.add_argument("--base", metavar="REF", help="Git base ref; defaults to the origin default branch")
    diff.add_argument("--profiles-dir", type=Path, help="directory containing profiles.yml")
    diff.add_argument("--target", help="dbt target override")
    diff.add_argument(
        "--execution-project",
        metavar="PROJECT",
        help="BigQuery project for RowRay jobs; does not change the dbt target",
    )
    diff.add_argument("--vars", dest="dbt_vars", metavar="YAML", help="dbt variables used for incremental compiles and full-refresh defaults")
    diff.add_argument("--warn-error-options", metavar="YAML", help="dbt v1 warning options passed unchanged to all compiles")
    diff.add_argument("--full-refresh", action="store_true", help="also estimate full-refresh scan cost for both versions")
    diff.add_argument(
        "--full-refresh-vars",
        metavar="YAML",
        help="top-level variables merged over --vars for full-refresh compiles; requires --full-refresh",
    )
    diff.add_argument("--execute", action="store_true", help="run the potentially billed incremental data comparison after a dry run")
    diff.add_argument("--column", metavar="NAME", help="compare actual row counts per value; requires --execute")
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
                args.column,
                args.full_refresh_vars,
                args.execution_project,
            )
        )
        return 0
    except (OSError, RowRayError, yaml.YAMLError, ValueError) as error:
        print(f"rowray: {error}", file=sys.stderr)
        return 1
    except GoogleAPICallError as error:
        print(f"rowray: {format_google_error(error)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
