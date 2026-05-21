import subprocess
import unittest
from pathlib import Path


class RepoGenericityTests(unittest.TestCase):
    def test_tracked_benchmark_files_do_not_contain_personal_remote_identifiers(self) -> None:
        proc = subprocess.run(["git", "ls-files", "-z"], check=False, capture_output=True)
        if proc.returncode != 0:
            self.skipTest("tracked-file genericity check requires a Git checkout")
        tracked = [Path(item.decode("utf-8")) for item in proc.stdout.split(b"\0") if item]
        bad_tokens = [
            "zhou" + "you@" + ".".join(["10", "34", "23", "184"]),
            "zhou" + "you5090pc",
            "/home/" + "zhouyou/",
        ]
        offenders = []
        for path in tracked:
            if not path.exists() or not (path == Path("benchmark") or Path("benchmark") in path.parents):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in bad_tokens:
                if token in text:
                    offenders.append(f"{path}: {token}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
