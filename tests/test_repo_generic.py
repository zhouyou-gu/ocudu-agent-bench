import subprocess
import unittest
from pathlib import Path


class RepoGenericityTests(unittest.TestCase):
    def test_tracked_benchmark_files_do_not_contain_personal_remote_identifiers(self) -> None:
        proc = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True)
        tracked = [Path(item.decode("utf-8")) for item in proc.stdout.split(b"\0") if item]
        searched_roots = {
            Path("benchmark"),
            Path(".config.example"),
            Path(".gitignore"),
        }
        bad_tokens = [
            "zhou" + "you@" + ".".join(["10", "34", "23", "184"]),
            "zhou" + "you5090pc",
            "/home/" + "zhouyou/",
        ]

        offenders = []
        for path in tracked:
            if not path.exists():
                continue
            if not any(path == root or root in path.parents for root in searched_roots):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in bad_tokens:
                if token in text:
                    offenders.append(f"{path}: {token}")

        self.assertEqual(offenders, [])

    def test_tracked_benchmark_files_do_not_keep_stale_srsran_project_sut_paths(self) -> None:
        proc = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True)
        tracked = [Path(item.decode("utf-8")) for item in proc.stdout.split(b"\0") if item]
        searched_roots = {
            Path("benchmark"),
            Path(".config.example"),
        }
        allowed = {
            Path("benchmark/tests/test_config.py"),
            Path("benchmark/tests/test_repo_generic.py"),
            Path("benchmark/tests/test_remote.py"),
        }
        bad_tokens = [
            "srsRAN_Project",
            "srsran-project-build",
            "sources/srsran-project",
            "install/srsran-project",
        ]

        offenders = []
        for path in tracked:
            if not path.exists():
                continue
            if path in allowed:
                continue
            if not any(path == root or root in path.parents for root in searched_roots):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in bad_tokens:
                if token in text:
                    offenders.append(f"{path}: {token}")

        self.assertEqual(offenders, [])

    def test_active_benchmark_files_do_not_reference_removed_external_provider(self) -> None:
        proc = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True)
        tracked = [Path(item.decode("utf-8")) for item in proc.stdout.split(b"\0") if item]
        searched_roots = {
            Path("benchmark"),
            Path(".config.example"),
        }
        bad_tokens = ["d" + "rax", "d" + "RAX", "D" + "rax", "DR" + "AX"]

        offenders = []
        for path in tracked:
            if not path.exists():
                continue
            if not any(path == root or root in path.parents for root in searched_roots):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in bad_tokens:
                if token in text:
                    offenders.append(f"{path}: {token}")

        self.assertEqual(offenders, [])

    def test_benchmark_repo_does_not_embed_flexric_patch_scripts(self) -> None:
        proc = subprocess.run(["git", "ls-files", "-z", "--", "benchmark"], check=True, capture_output=True)
        tracked = [Path(item.decode("utf-8")) for item in proc.stdout.split(b"\0") if item]
        bad_tokens = [
            "apply_" + "kpm_v05_patch.py",
            "def generate_flexric_" + "kpm_v05_patch_script",
            "def generate_ocudu_" + "kpm_v05_decoder_source",
        ]

        offenders = []
        for path in tracked:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in bad_tokens:
                if token in text:
                    offenders.append(f"{path}: {token}")

        self.assertEqual(offenders, [])

    def test_standalone_benchmark_markdown_does_not_link_to_parent_research_docs(self) -> None:
        bad_tokens = [
            "../skillful-" + "ran-research",
            "skillful-" + "ran-research/benchmark_design/benchmark_" + "design.md",
            "skillful-" + "ran-research/benchmark_design/benchmark_" + "architecture.md",
            "skillful-" + "ran-research/benchmark_design/remote_" + "ocudu_api_setup.md",
        ]
        offenders = []
        for path in Path("benchmark").rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in bad_tokens:
                if token in text:
                    offenders.append(f"{path}: {token}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
