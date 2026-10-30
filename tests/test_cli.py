import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rowray.cli import compiled_sql, report, resolve_env


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


if __name__ == "__main__":
    unittest.main()
