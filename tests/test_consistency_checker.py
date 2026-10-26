import subprocess
import sys
from pathlib import Path


def test_checkout_matches_its_runtime_manifest():
    repo = Path(__file__).parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(repo / "e2e" / "check_consistency.py"),
            "--skill-dir",
            str(repo),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
