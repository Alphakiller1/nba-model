"""scripts/prefer_newer_snapshots.py: a CI cache restore must not replace a
newer committed league snapshot."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prefer_newer_snapshots.py"


def load(root: Path):
    spec = importlib.util.spec_from_file_location("prefer_newer_snapshots", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    return module


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class SnapshotPreferenceTests(unittest.TestCase):
    def test_newer_commit_wins_and_newer_cache_stays(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git(root, "init", "-q")
            stats, cover = root / "data/nbastats/2027", root / "data/coverage/nba-zone/2027"
            stats.mkdir(parents=True)
            cover.mkdir(parents=True)
            (stats / "bio.json").write_text(json.dumps({"fetched_at_utc": "2026-11-18T21:40:40+00:00", "v": "new"}))
            (cover / "offense.json").write_text(json.dumps({"retrieved_at_utc": "2026-11-09T00:00:00+00:00", "v": "old"}))
            git(root, "add", ".")
            git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "seed")
            # The cache restore: an older table over a newer commit, a newer capture over an older one.
            (stats / "bio.json").write_text(json.dumps({"fetched_at_utc": "2026-11-07T03:30:13+00:00", "v": "cached"}))
            (cover / "offense.json").write_text(json.dumps({"retrieved_at_utc": "2026-11-12T00:00:00Z", "v": "cached"}))
            put_back = load(root).prefer_newer()
            self.assertEqual(put_back, ["data/nbastats/2027/bio.json"])
            self.assertEqual(json.loads((stats / "bio.json").read_text())["v"], "new")
            self.assertEqual(json.loads((cover / "offense.json").read_text())["v"], "cached")


if __name__ == "__main__":
    unittest.main()
