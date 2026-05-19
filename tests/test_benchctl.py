import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from benchmark import benchctl


class BenchctlTests(unittest.TestCase):
    def test_tasks_list_json(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = benchctl.main(["--json", "tasks", "list"])

        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "ok")
        self.assertTrue(any(task["task_id"] == "ws_prb_ping_v1" for task in output["tasks"]))

    def test_remote_check_no_probe_parses_section_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Path(tmpdir) / "config"
            config.write_text(
                "\n".join(["remote:", "    ssh user@example", "    workspace ~/bench-workspace"]),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = benchctl.main(["--json", "remote", "check", "--config", str(config), "--no-probe"])

        self.assertEqual(code, 0)
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["status"], "configured")
        self.assertEqual(output["ssh_target"], "user@example")
        self.assertEqual(output["workspace"], "~/bench-workspace")


if __name__ == "__main__":
    unittest.main()
