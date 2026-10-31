import argparse
import sys
from pathlib import Path

import yaml

from rowray.compare import RowRayError, compare


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="rowray", description="Compare one dbt model against a Git base ref")
    subcommands = result.add_subparsers(dest="command", required=True)
    diff = subcommands.add_parser("diff", help="compare BASE with the current working tree")
    diff.add_argument("--select", required=True, metavar="MODEL", help="one dbt model name")
    diff.add_argument("--base", metavar="REF", help="Git base ref; defaults to the origin default branch")
    diff.add_argument("--profiles-dir", type=Path, help="directory containing profiles.yml")
    diff.add_argument("--target", help="dbt target override")
    diff.add_argument("--vars", dest="dbt_vars", metavar="YAML", help="dbt variables passed unchanged to both compiles")
    diff.add_argument("--warn-error-options", metavar="YAML", help="dbt warning options passed unchanged to both compiles")
    diff.add_argument("--full-refresh", action="store_true", help="compile both versions with dbt full-refresh semantics")
    diff.add_argument("--execute", action="store_true", help="execute aggregate data profiles after a guarded dry run")
    diff.add_argument("--column", metavar="NAME", help="compare row counts per value; requires --execute")
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
            )
        )
        return 0
    except (OSError, RowRayError, yaml.YAMLError, ValueError) as error:
        print(f"rowray: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
