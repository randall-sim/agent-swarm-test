import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES))
spec = importlib.util.spec_from_file_location("prepare_demo", EXAMPLES / "prepare_demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


class DemoTests(unittest.TestCase):
    def test_all_demos_prepare_clean_repositories_and_have_failing_acceptance_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            for kind in demo.DEMOS:
                with self.subTest(kind=kind):
                    path = demo.prepare(kind, "example-" + kind, tmp)
                    self.assertEqual(demo.git(path, "status", "--porcelain"), "")
                    config = json.loads((path / "demo.json").read_text())
                    self.assertEqual(config["workers"], 3)
                    result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                                            cwd=path, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("FAILED", result.stderr)
                    self.assertNotIn("ImportError", result.stderr)
                    self.assertNotIn("SyntaxError", result.stderr)
                    self.assertEqual(demo.git(path, "status", "--porcelain"), "")
                    self.assertIn("--env-file", (path / "DEMO.md").read_text())

    def test_existing_destination_and_path_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = demo.prepare("taskboard", "keep", tmp)
            original = (path / "api.py").read_bytes()
            with self.assertRaises(FileExistsError):
                demo.prepare("taskboard", "keep", tmp)
            self.assertEqual((path / "api.py").read_bytes(), original)
            for name in ("../escape", "a/b", "", ".git", "a b"):
                with self.assertRaises(ValueError):
                    demo.prepare("taskboard", name, tmp)
