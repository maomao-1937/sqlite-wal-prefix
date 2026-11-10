"""Run the documented examples and verify exact report bytes."""

import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    checks = json.loads((root / "checks.json").read_text(encoding="utf-8"))
    for check in checks:
        process = subprocess.run([sys.executable, *check["args"]], cwd=root, capture_output=True, timeout=15)
        expected = json.dumps(check["report"], separators=(",", ":")).encode("ascii") + b"\n"
        if (process.returncode, process.stdout, process.stderr) != (check["exit"], expected, b""):
            print(f"FAIL {check['name']} (exit {process.returncode})", file=sys.stderr)
            return 1
        print(f"PASS {check['name']} (exit {process.returncode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
