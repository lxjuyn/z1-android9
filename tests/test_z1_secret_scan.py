import subprocess
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts/z1_secret_scan.py"


class SecretScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="z1-secret-scan-")
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.add("clean.txt", "ordinary display documentation\n")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=test", "-c", "user.email=test@localhost", "commit", "-qm", "clean"], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        subprocess.run(["git", "-C", str(self.root), "add", name], check=True)

    def run_scan(self):
        return subprocess.run(["python3", str(TOOL), "--staged", "--root", str(self.root)], capture_output=True, text=True)

    def test_clean_change_passes(self):
        self.add("next.txt", "boot-only display takeover candidate\n")
        result = self.run_scan()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("test@localhost", result.stdout + result.stderr)

    def test_github_token_is_rejected(self):
        self.add("leak.txt", "github_pat_" + "A" * 80 + "\n")
        result = self.run_scan()
        self.assertEqual(result.returncode, 1)
        self.assertIn("leak.txt", result.stderr)
        self.assertNotIn("A" * 20, result.stdout + result.stderr)

    def test_tree_history_is_scanned(self):
        self.add("old.txt", "-----BEGIN " + "OPENSSH " + "PRIVATE KEY-----\n")
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=test", "-c", "user.email=test@localhost", "commit", "-qm", "contains private key"], check=True)
        result = subprocess.run(["python3", str(TOOL), "--tree", "HEAD", "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("PRIVATE KEY-----", result.stdout)


if __name__ == "__main__":
    unittest.main()
