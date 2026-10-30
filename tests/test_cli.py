import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from rowray.cli import compile_model, compiled_sql, report, resolve_env


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

        output = report(1024**3, 2 * 1024**3)
        self.assertIn("1.0 GiB", output)
        self.assertIn("2.0 GiB", output)
        self.assertIn("+100.0%", output)

        with patch.dict("os.environ", {"BQ_PROJECT": "analytics"}):
            self.assertEqual(resolve_env("{{ env_var('BQ_PROJECT') }}"), "analytics")

    @patch("rowray.cli.compiled_sql", return_value="select 1")
    @patch("rowray.cli.run")
    def test_passes_vars_to_dbt_compile(self, run, _compiled_sql):
        project = Path("/project")
        compile_model("dbt", project, Path("/target"), "orders", Path("/profiles"), None, "{day: 1}")
        self.assertEqual(
            run.call_args,
            call(
                [
                    "dbt",
                    "compile",
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
                ],
                project,
            ),
        )


if __name__ == "__main__":
    unittest.main()
