import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rowray.compare import compare


DBT_COMPAT = os.environ.get("ROWRAY_DBT_COMPAT")
FIXTURE = Path(__file__).parent / "fixtures" / "dbt_compat"


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@unittest.skipUnless(DBT_COMPAT in {"v1", "v2"}, "set ROWRAY_DBT_COMPAT to v1 or v2")
class DbtCompatibilityTest(unittest.TestCase):
    def test_compiles_base_and_worktree_with_real_dbt(self):
        python = Path(shutil.which("dbt")).with_name("python")
        package = "dbt-core" if DBT_COMPAT == "v1" else "dbt"
        version = subprocess.run(
            [python, "-c", f"import importlib.metadata; print(importlib.metadata.version('{package}'))"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(version, "1.10.23" if DBT_COMPAT == "v1" else "2.0.9")
        python_version = subprocess.run(
            [python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(python_version, "3.12")

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            shutil.copytree(FIXTURE, project)
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            subprocess.run(["git", "add", "."], cwd=project, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=RowRay",
                    "-c",
                    "user.email=rowray@example.com",
                    "commit",
                    "-qm",
                    "baseline",
                ],
                cwd=project,
                check=True,
            )
            (project / "models" / "example.sql").write_text("select 1 as id, 'rowray' as name\n")

            def dry_run(_client, sql):
                columns = [("id", "INTEGER", "NULLABLE")]
                if "as name" in sql:
                    columns.append(("name", "STRING", "NULLABLE"))
                return 1, columns

            with (
                working_directory(project),
                patch("rowray.compare.bigquery_client", return_value=object()),
                patch("rowray.compare.dry_run", side_effect=dry_run),
                redirect_stdout(StringIO()),
            ):
                output = compare(
                    "example",
                    "HEAD",
                    project / "profiles" / DBT_COMPAT,
                    None,
                    None,
                    None,
                    False,
                    False,
                    None,
                )

        self.assertIn("added: name", output)


if __name__ == "__main__":
    unittest.main()
