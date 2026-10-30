import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import call, patch

from rowray.cli import RowRayError, compile_model, compile_refs, compiled_sql, focus_report, focus_sql, profile_sql, progress, report, resolve_env, run as run_command


class CliTest(unittest.TestCase):
    def test_reads_model_and_renders_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "manifest.json").write_text(
                json.dumps(
                    {
                        "nodes": {
                            "model.shop.orders": {
                                "resource_type": "model",
                                "name": "orders",
                                "compiled_code": "select * from raw.orders",
                            }
                        }
                    }
                )
            )
            self.assertEqual(compiled_sql(target, "orders"), "select * from raw.orders")

        output = report(
            1024**3,
            2 * 1024**3,
            [("id", "INTEGER", "REQUIRED"), ("old", "STRING", "NULLABLE")],
            [("id", "STRING", "REQUIRED"), ("new", "BOOLEAN", "NULLABLE")],
            [],
            [],
            None,
            "Profile not run",
        )
        self.assertIn("1.0 GiB", output)
        self.assertIn("2.0 GiB", output)
        self.assertIn("+100.0%", output)
        self.assertIn("added: new", output)
        self.assertIn("removed: old", output)
        self.assertIn("type changes: id INTEGER → STRING", output)

        sql = profile_sql("select 1 as amount", "select 2 as amount", [("amount", "INTEGER")])
        self.assertIn("APPROX_COUNT_DISTINCT(`amount`)", sql)
        self.assertIn("SUM(`amount`)", sql)

        profile = {
            "base": {"n_rows": 10, "amount__nulls": 1, "amount__distincts": 4, "amount__sum": 100},
            "head": {"n_rows": 12, "amount__nulls": 0, "amount__distincts": 5, "amount__sum": 120},
        }
        output = report(1, 1, [], [], [("amount", "INTEGER")], [], profile, "")
        self.assertIn("rows: 10 → 12 (+20.0%)", output)
        self.assertIn("│ amount │ 10.0% → 0.0% │     4 → 5 │ 100 → 120 │", output)

        sql = focus_sql("select 'home' as page_type", "select 'product' as page_type", "page_type")
        self.assertIn("GROUP BY value", sql)
        output = focus_report(
            "page_type",
            [
                {"value": "home", "base_rows": 100, "head_rows": 80},
                {"value": "product", "base_rows": 0, "head_rows": 25},
            ],
        )
        self.assertIn("│ home    │       100 │            80 │    -20.0% │", output)
        self.assertIn("│ product │         0 │            25 │       new │", output)

        with patch.dict("os.environ", {"BQ_PROJECT": "analytics"}):
            self.assertEqual(resolve_env("{{ env_var('BQ_PROJECT') }}"), "analytics")

        output = StringIO()
        with patch("rowray.cli.time.monotonic", side_effect=[1.0, 2.0]), redirect_stdout(output):
            with progress("Compile BASE"):
                pass
        self.assertEqual(output.getvalue(), "→ Compile BASE...\n✓ Compile BASE (1.0s)\n")

    @patch("rowray.cli.compiled_sql", return_value="select 1")
    @patch("rowray.cli.run")
    def test_passes_options_to_dbt_compile(self, run, _compiled_sql):
        project = Path("/project")
        compile_model(
            "dbt",
            project,
            Path("/target"),
            "orders",
            Path("/profiles"),
            None,
            "{day: 1}",
            '{"silence":["Deprecation"]}',
            True,
            False,
        )
        self.assertEqual(
            run.call_args,
            call(
                [
                    "dbt",
                    "compile",
                    "--full-refresh",
                    "--project-dir",
                    "/project",
                    "--profiles-dir",
                    "/profiles",
                    "--target-path",
                    "/target",
                    "--select",
                    "orders",
                    "--vars",
                    "{day: 1}",
                    "--warn-error-options",
                    '{"silence":["Deprecation"]}',
                ],
                project,
            ),
        )

    @patch("rowray.cli.compile_model")
    def test_compiles_refs_in_parallel(self, compile_model):
        barrier = threading.Barrier(2)

        def compile_ref(*arguments):
            barrier.wait(timeout=5)
            return str(arguments[1])

        compile_model.side_effect = compile_ref
        arguments = ("dbt", Path("/base"), Path("/target"), "orders", Path("/profiles"), None, None, None, False, False)
        result = compile_refs({"base": arguments, "head": arguments[:1] + (Path("/head"),) + arguments[2:]})
        self.assertEqual(result, {"base": "/base", "head": "/head"})

    @patch("rowray.cli.subprocess.run", return_value=CompletedProcess([], 1, "dbt error", "dependency warning"))
    def test_reports_dbt_error_before_warnings(self, _subprocess_run):
        with self.assertRaises(RowRayError) as context:
            run_command(["dbt", "compile"], Path("/project"))
        self.assertLess(str(context.exception).index("dbt error"), str(context.exception).index("dependency warning"))


if __name__ == "__main__":
    unittest.main()
